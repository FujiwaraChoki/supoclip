"""Frame rounding must not reject otherwise complete cached sections."""
import json
import logging
import unittest
from pathlib import Path

from src import youtube_source as source
from src.observability import JsonLogFormatter


class SectionBoundaryTests(unittest.TestCase):
    def test_real_video_tail_short_by_52ms_is_covered_without_redownload(self):
        piece = source.CachedPiece(2242.367, 2298.816, Path('tail.mp4'))
        needed = (2244.640, 2298.868)
        self.assertEqual(source.missing_intervals([(piece.start, piece.end)], [needed]), [])
        chosen = source._cover_pieces([piece], *needed)
        self.assertEqual(chosen, [piece])
        self.assertEqual(source._plan_slices(chosen, *needed), [(piece, needed[0], piece.end)])

    def test_real_missing_tail_is_not_marked_complete(self):
        piece = source.CachedPiece(0, 9.7, Path('short.mp4'))
        self.assertEqual(source.missing_intervals([(0, 9.7)], [(0, 10)]), [(9.7, 10)])
        self.assertIsNone(source._cover_pieces([piece], 0, 10))
        self.assertEqual(source._plan_slices([piece], 0, 10), [])

    def test_subframe_gap_between_fragments_does_not_download_again(self):
        first = source.CachedPiece(0, 5, Path('first.mp4'))
        second = source.CachedPiece(5.052, 10, Path('second.mp4'))
        self.assertEqual(source.missing_intervals([(0, 5), (5.052, 10)], [(0, 10)]), [])
        self.assertEqual(source._cover_pieces([first, second], 0, 10), [first, second])

    def test_real_missing_middle_is_not_accepted_by_cover(self):
        first = source.CachedPiece(0, 5, Path('first.mp4'))
        second = source.CachedPiece(5.2, 10, Path('second.mp4'))
        self.assertEqual(source.missing_intervals([(0, 5), (5.2, 10)], [(0, 10)]), [(5, 5.2)])
        self.assertIsNone(source._cover_pieces([first, second], 0, 10))

    def test_materialization_error_type_survives_json_formatting(self):
        record = logging.LogRecord('test', logging.WARNING, __file__, 1, 'Section failed', (), None)
        record.youtube_error_type = 'YouTubeDownloadError'
        record.task_id = 'task'
        record.youtube_download_kind = 'section_materialization'
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload['youtube_error_type'], 'YouTubeDownloadError')
        self.assertEqual(payload['task_id'], 'task')
