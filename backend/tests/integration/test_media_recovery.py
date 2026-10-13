"""Recovery preserves migrated originals, committed clips and staged cleanup."""

from contextlib import asynccontextmanager
from pathlib import Path
import json
import subprocess
import uuid

import pytest
from sqlalchemy import text

from src.editor_document import atomic_json, editor_dir
from src.media_storage import MediaStorage
from src.repositories.clip_repository import ClipRepository
from src.services.editor_service import prepare_editor, combine_editor, read_state
from src.workers.media_cleanup import drain_deletions, reference_index, referenced
from tests.fixtures.factories import create_user, create_source, create_task
from tests.integration.test_media_deletion_transactions import media_backend


async def test_upgrade_adds_columns_to_previously_applied_outbox(initialized_database):
    schema = 'media_upgrade_' + uuid.uuid4().hex
    async with initialized_database.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
        await connection.execute(text(f'SET LOCAL search_path TO {schema}'))
        for sql in ['CREATE TABLE users (id TEXT PRIMARY KEY)',
                    'CREATE TABLE generated_clips (file_path TEXT)',
                    'CREATE TABLE sources (url TEXT)',
                    'CREATE TABLE media_deletions (file_path TEXT PRIMARY KEY, is_prefix BOOLEAN, created_at TIMESTAMPTZ)']:
            await connection.execute(text(sql))
        migration = Path(__file__).parents[2] / 'src/migrations/sql/20261014_0001_media_storage_recovery.sql'
        for _ in range(2):
            for statement in migration.read_text().split(';'):
                if statement.strip():
                    await connection.execute(text(statement))
        columns = (await connection.execute(text('SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = :table'), {'schema': schema, 'table': 'media_deletions'})).scalars().all()
        assert {'attempts', 'next_attempt_at', 'hold_token'} <= set(columns)
        await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))


async def test_migration_skips_oversized_original_without_uploading_it(db_session, media_backend):
    from scripts.migrate_media_to_s3 import migrate
    storage = media_backend
    user = await create_user(db_session)
    path = Path(storage.config.temp_dir) / 'uploads' / 'oversized.mp4'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'oversized original')
    source = await create_source(db_session, source_type='video_url', url='upload://oversized.mp4')
    task = await create_task(db_session, user_id=user['id'], source_id=source['id'], status='completed')
    await db_session.commit()
    storage.config.max_video_upload_bytes = 1
    first = await migrate(apply=True, task_id=task['id'])
    second = await migrate(apply=True, task_id=task['id'])
    assert first['skipped'] == second['skipped'] == 1
    assert first['sources'] == second['sources'] == 0
    assert path.exists()
    assert not storage.client.objects


async def test_invalid_migration_upload_is_removed_before_retry(db_session, media_backend, monkeypatch):
    from scripts.migrate_media_to_s3 import migrate
    user = await create_user(db_session)
    path = Path(media_backend.config.temp_dir) / 'uploads' / 'invalid.mp4'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original')
    source = await create_source(db_session, source_type='video_url', url='upload://invalid.mp4')
    task = await create_task(db_session, user_id=user['id'], source_id=source['id'], status='completed')
    await db_session.commit()
    def invalid(*args):
        raise ValueError('Invalid uploaded source metadata')
    monkeypatch.setattr(MediaStorage, 'validate_source', invalid)
    for _ in range(2):
        counts = await migrate(apply=True, task_id=task['id'])
        assert counts['skipped'] == 1 and counts['sources'] == 0
        assert not media_backend.client.objects
        assert path.exists()


async def test_analysis_failure_preserves_paid_word_timings(db_session, media_backend, monkeypatch):
    from src.services.video_service import VideoService
    from src.services import video_service
    from src.media_storage import media_work, SIDECARS
    storage = media_backend
    url = 'upload://s3/owner/upload/video.mp4'
    key = storage.parse(storage.source_reference(url))
    storage.client.objects[key] = b'original'
    monkeypatch.setattr(VideoService, '_get_file_duration', lambda _: 2)
    async def transcribe(path, **kwargs):
        path.with_suffix('.transcript_cache.json').write_text('{"words": [{"text": "hello", "start": 0, "end": 1}]}')
        return 'hello'
    async def fail_analysis(*args, **kwargs):
        raise RuntimeError('Injected analysis failure')
    monkeypatch.setattr(VideoService, 'generate_transcript', transcribe)
    monkeypatch.setattr(VideoService, 'analyze_transcript', fail_analysis)
    monkeypatch.setattr(video_service, 'build_clip_signal_summary', lambda *args: None)
    @media_work
    async def pipeline():
        return await VideoService.process_video_complete(url=url, source_type='video_url')
    with pytest.raises(Exception, match='Injected analysis failure'):
        await pipeline()
    assert b'hello' in storage.client.objects[str(Path(key).with_suffix(SIDECARS[1]))]
    assert not list((Path(storage.config.temp_dir) / 'media-cache').glob('*/video.mp4'))


async def test_sweep_index_checks_live_media_without_per_object_queries(db_session, media_backend):
    from unittest.mock import AsyncMock
    user = await create_user(db_session)
    source = await create_source(db_session, source_type='video_url', url='upload://source.mp4')
    task = await create_task(db_session, user_id=user['id'], source_id=source['id'], status='completed')
    path = Path(media_backend.config.temp_dir) / 'clip.mp4'
    path.write_bytes(b'clip')
    clip_id = await ClipRepository.create_clip(db_session, task['id'], path.name, str(path), '00:00', '00:01', 1, '', 0, '', 1)
    await db_session.commit()
    clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
    index = await reference_index(db_session, media_backend)
    never_query = AsyncMock(side_effect=AssertionError('Unexpected per-object query'))
    assert await referenced(never_query, clip['file_path'], media_backend, index=index)
    assert not await referenced(never_query, media_backend.reference(media_backend.key('clips', 'other', 'video.mp4')), media_backend, index=index)


async def test_autosave_finishes_while_another_connection_holds_task_edit_lock(client, db_session, initialized_database, media_backend, auth_headers):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.editor_document import EditDocument
    from src.repositories.edit_transaction import task_edit_transaction
    user = await create_user(db_session, user_id='user-1')
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=user['id'], source_id=source['id'], status='completed')
    path = Path(media_backend.config.temp_dir) / 'clip.mp4'
    path.write_bytes(b'clip')
    clip_id = await ClipRepository.create_clip(db_session, task['id'], path.name, str(path), '00:00', '00:02', 2, '', 0, '', 1)
    await db_session.commit()
    directory = editor_dir(media_backend.config.temp_dir, task['id'], clip_id, path.name)
    document = EditDocument.model_validate({'segments': [{'id': 'one', 'start': 0, 'end': 2}]}).model_dump()
    atomic_json(directory / 'asset.json', {'status': 'ready', 'duration': 2})
    atomic_json(directory / 'original.json', document)
    atomic_json(directory / 'draft.json', {'revision': 0, 'document': document})
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with sessions() as locked:
        async with task_edit_transaction(locked, task['id']):
            response = await asyncio.wait_for(client.patch(f"/tasks/{task['id']}/clips/{clip_id}/editor",
                json={'basis': path.name, 'revision': 0, 'document': document}, headers=auth_headers), timeout=2)
            assert response.status_code == 200 and response.json()['revision'] == 1


async def test_failed_combine_queues_staged_objects_and_uploads_outside_edit_lock(db_session, media_backend, monkeypatch):
    from src.repositories.edit_transaction import task_edit_transaction
    from src.repositories import edit_transaction
    storage = media_backend
    source_path = Path(storage.config.temp_dir) / 'uploads' / 'source.mp4'
    source_path.parent.mkdir(parents=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:size=320x180:rate=24:duration=1', '-c:v', 'libx264', str(source_path)], check=True, capture_output=True)
    user = await create_user(db_session)
    source = await create_source(db_session, source_type='video_url', url='upload://source.mp4')
    task = await create_task(db_session, user_id=user['id'], source_id=source['id'], status='completed')
    entries = []
    for number in range(2):
        clip_id = await ClipRepository.create_clip(db_session, task['id'], f'clip-{number}.mp4', str(source_path), '00:00', '00:01', 1, 'hello', 0, '', number)
        await db_session.commit()
        await prepare_editor({}, task['id'], clip_id, f'clip-{number}.mp4')
        state = read_state(editor_dir(storage.config.temp_dir, task['id'], clip_id, f'clip-{number}.mp4'))
        assert state['status'] == 'ready'
        entries.append({'id': clip_id, 'filename': f'clip-{number}.mp4', 'document': state['draft']['document'],
                        'state': {k: state[k] for k in ('width', 'height', 'hasAudio')}, 'ranges': [[0, 1]]})
    directory = editor_dir(storage.config.temp_dir, task['id'], entries[0]['id'], entries[0]['filename'])
    job = uuid.uuid4().hex
    atomic_json(directory / f'request-{job}.json', {'inputs': entries})
    atomic_json(directory / f'job-{job}.json', {'status': 'queued'})
    inside = False
    @asynccontextmanager
    async def observed_transaction(*args, **kwargs):
        nonlocal inside
        async with task_edit_transaction(*args, **kwargs):
            inside = True
            try:
                yield
            finally:
                inside = False
    monkeypatch.setattr(edit_transaction, 'task_edit_transaction', observed_transaction)
    upload = storage.client.upload_file
    def assert_unlocked(*args, **kwargs):
        assert not inside, 'Remote upload held the task mutation lock'
        return upload(*args, **kwargs)
    storage.client.upload_file = assert_unlocked
    async def fail_commit(*args):
        raise RuntimeError('Injected pre-commit failure')
    monkeypatch.setattr(ClipRepository, 'reorder_task_clips', fail_commit)
    await combine_editor({}, task['id'], entries[0]['id'], entries[0]['filename'], job)
    assert json.loads((directory / f'job-{job}.json').read_text())['status'] == 'failed'
    assert len(await ClipRepository.get_clips_by_task(db_session, task['id'])) == 2
    await drain_deletions(db_session, storage)
    assert not (directory / f'export-{job}.mp4').exists()
    assert storage.parse(storage.editor_reference(directory, f'export-{job}.mp4')) not in storage.client.objects
    assert (directory / 'clean.mp4').exists()
