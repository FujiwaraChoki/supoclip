# Clipping Gods

A standalone, Render-ready video clipping studio. Paste a **public** YouTube,
TikTok, or Facebook video URL; Clipping Gods downloads the source, transcribes
its audio, asks a GonkaRouter team of one scout and five reviewers to find and
rank strong moments, and renders vertical MP4 clips.

The project is isolated from the existing SupoClip application in this folder.
It is a deliberately focused rebuild of the core import-to-clips workflow, not
a port of SupoClip accounts, billing, social publishing, editor, or analytics.

## What the team does

1. **Scout** reviews transcript windows and proposes candidate clips.
2. **Hook editor** reviews openings.
3. **Context editor** checks each moment stands alone.
4. **Story editor** checks narrative and payoff.
5. **Audience editor** scores concrete and shareable value.
6. **Source-fidelity editor** checks that the clip preserves the source meaning.

The five reviewers send independent requests to the configured Gonka model.
Available reviewer scores are averaged; processing continues if one reviewer
fails or times out, with the remaining four reviews required to rank candidates.
Two failed reviewers stop the job rather than returning an under-reviewed result.
The scout uses one request per
eight-minute transcript window, so long videos make additional requests. This
uses **one** Gonka API key and one selected model, not five model keys. Check
your gateway's current usage and pricing before processing long videos.

## How to set it up and run locally

You need Python 3.11 or newer, `ffmpeg`/`ffprobe`, and API credentials for
GonkaRouter and AssemblyAI. On Ubuntu or Debian, install FFmpeg with:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv
```

Clone the GitHub repository and enter the app folder:

```bash
git clone --branch feature/clipping-gods-standalone --single-branch \
  https://github.com/wwwwrrr123/clipping-gods.git
cd clipping-gods/clipping-gods
```

Create a local environment file and add your keys and a private app password:

```bash
cp .env.example .env
```

Edit `.env` and set `GONKA_API_KEY`, `ASSEMBLYAI_API_KEY`, and `APP_PASSWORD`.
Keep this file private; `.env` is ignored by Git.

Create the Python environment and launch the app:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./run.sh
```

Open [http://localhost:8000](http://localhost:8000), enter your `APP_PASSWORD`,
paste a public video link, then select **Make Clips**. The app shows job
progress and lets you preview or download finished clips. Stop the local server
with `Ctrl+C`.

`run.sh` creates the virtual environment if needed, installs dependencies when
they are missing, and starts the web app. Since keys load from `.env`, you do
not need to export them in every terminal.

### Setup without `.env`

You can provide the settings as environment variables instead:

```bash
export GONKA_API_KEY="your-gateway-key"
export ASSEMBLYAI_API_KEY="your-transcription-key"
export APP_PASSWORD="choose-a-private-password"
./run.sh
```

Use the password gate before making the app reachable from another device or
the public internet. Do not put credentials in the source files or commit them
to Git.

## Render deployment

The `render.yaml` Blueprint defines a Docker web service with a 10 GB persistent
disk and a generated app password. To deploy the app already in this repository:

1. Sign in to Render and choose **New → Blueprint**.
2. Connect the `wwwwrrr123/clipping-gods` GitHub repository.
3. Set the Blueprint file path to `clipping-gods/render.yaml` and select the
   `feature/clipping-gods-standalone` branch (or the branch you intend to deploy).
4. In the service environment settings, add `GONKA_API_KEY` and
   `ASSEMBLYAI_API_KEY` as secret values.
5. Apply the Blueprint and wait for the service health check to pass.
6. Open the Render service URL. Find the generated `APP_PASSWORD` in the
   service's environment settings and use it to unlock the studio.

The persistent disk requires a paid Render web-service plan. This single
service keeps its SQLite job database and generated media on that disk. It is
intended for a personal, single-instance workspace, not a multi-user service.
Back up the disk and set an application password before sharing the URL.

Render's free web instances have ephemeral filesystems, can spin down, and do
not support persistent disks. They can serve a disposable demo, but do not
provide durable job/media storage. A free Render Postgres also expires after
30 days and would not solve persistent media requirements. The Blueprint uses
a paid `1c-2g` instance for durable storage; the instance and disk may incur
charges.

## Settings

| Variable | Default | Description |
|---|---|---|
| `APP_PASSWORD` | empty (local development only) | Protects job and clip APIs; set on any public deployment |
| `COOKIE_SECURE` | false | Marks the login cookie Secure; enabled by the Render Blueprint |
| `GONKA_API_KEY` | unset | Required GonkaRouter secret |
| `GONKA_BASE_URL` | `https://api.gonkarouter.io/v1` | OpenAI-compatible inference URL |
| `GONKA_MODEL` | `zai-org/GLM-5.3-Flash` | Model ID sent to GonkaRouter |
| `ASSEMBLYAI_API_KEY` | unset | Required speech-to-text provider secret |
| `DATA_DIR` | `data` | SQLite and media root; Render mounts its persistent disk at `/var/data` |
| `MAX_VIDEO_BYTES` | `1000000000` | Maximum downloaded source size |
| `MAX_VIDEO_SECONDS` | `5400` | Maximum source duration (90 minutes) |
| `MAX_CLIPS` | `8` | Maximum clips to render per video |

Only public video links from the supported platform hosts are accepted.
Private, login-gated, or unavailable videos are not supported. Use content you
own or have permission to download, edit, and publish. No platform API keys are
needed; GonkaRouter inference and AssemblyAI transcription credentials are
separate requirements.

## Limitations

- One video is processed at a time by the in-process worker.
- A service restart requeues interrupted work from its source URL.
- Generated clips and source files consume persistent-disk space; delete old
  jobs/media from the Render disk or local `DATA_DIR` when no longer needed.
- Clips are center-cropped to 9:16. This focused build does not yet include
  burned-in subtitles, face tracking, accounts, publishing, or the full editor.
- Follow platform terms and copyright law; a publicly reachable URL does not
  grant permission to reuse its contents.
