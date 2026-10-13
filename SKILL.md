---
name: loudscribe
description: Turn long recordings made in loud places (clubs, bars, festivals, parties; voice recorder such as a Tonfarb A20, or phone) into just the conversations, all on the user's own computer. Removes the music, finds where people are talking, cuts out those stretches into one conversation audio file, and writes a timestamped transcript saying where each conversation is in that file. Use when someone wants night-out recordings cleaned up, the music removed, the conversations extracted or transcribed, a moment found ("when did I meet…?"), or a night summarised or reviewed. Also works for quiet recordings (classes, cafés).
---

# loudscribe

You're helping someone, usually not technical, get their conversations out of noisy recordings.

## What you deliver (per night)

1. **Cleaned audio:** the music removed from each recording (a downloaded model, MelBand-RoFormer),
   levels evened out.
2. **The conversations found:** speech transcribed, junk and song lyrics dropped, the rest grouped
   into conversation sections.
3. **Those stretches, extracted** from the cleaned audio, and **joined into one file**
   (`conversations.m4a`), with a second of silence before each conversation. Each stretch runs from
   its first line to its last, so pauses of up to 20 s between lines stay in, with whatever was in
   them (now and then a few sung words, or someone else's chatter).
4. **A transcript file** (`transcript.txt`): every conversation with wall-clock times, and where each
   one starts in `conversations.m4a` and in the original recording.
5. **A way to find any moment:** `loudscribe find "words"` and `viewer.html` (search, click a line
   to hear it), plus per-recording versions of the audio and transcript.

A transcript alone isn't the job: the user should be able to listen to their conversations without
the music, and find any moment in the audio.

You do the technical work and make the decisions; they tell you where the recordings are, approve
installs, and answer plain questions. `SKILL_DIR` below means the folder containing this file.

- `RECORDER_SETUP.md`: how to set up the recorder next time (share it with the user).
- `loudscribe/` + `requirements.txt`: the tool, a Python package run as `python -m loudscribe`, with
  the models and settings that tested best. Tested on Apple Silicon Macs and a Windows 11 laptop
  (CPU); not yet run on Linux or with an NVIDIA GPU. You install it. If a computer needs something
  different, adapt it and tell the user what you changed.

## Rules (keep these whatever else you change)

- **Audio stays on this computer.** Never upload recordings or results, or paste transcripts into web
  tools or other services. Outputs go in a local folder (default `~/loudscribe-output/<night>`),
  never inside iCloud, OneDrive, Dropbox or Google Drive. The tool refuses the folders it recognises
  as synced, but can't spot every sync setup: check the output folder is local yourself.
- **Reading a transcript sends that text to your AI provider.** Transcripts hold other people's
  conversations. Before you first read transcript text, tell the user that in one sentence and ask.
  That includes `find` results, `index.json` and `viewer.html`, which contain the text too. For
  checks, use counts and the label/reasons columns of `segments.tsv` rather than the text.
- **Never modify, move or delete the original recordings.** Read them in place. If they're still on
  the recorder or in a cloud folder, ask the user to copy them to a normal local folder first.
  (On Windows, copy OneDrive files with `Copy-Item` or Explorer, not `robocopy /COPY:DAT`, which
  carries OneDrive's "Offline" flag onto the local copies.)
- Don't install or upgrade software while a processing run is going.
- Anything needing the user's password, acceptance of terms or a security dialog (Homebrew, `sudo`,
  winget, Windows UAC, macOS "allow access to Downloads/Desktop/removable volume" prompts) is theirs:
  say exactly what to type or click, in plain words, and wait.

## Talking to the user

Plain words ("music removal", not "source separation"), one action at a time. Before installing: what,
roughly how big (~4 GB, more with an NVIDIA GPU) and how long; ask. Before processing: how many
recordings and hours of audio, the time estimate, and that they can leave it running. Afterwards:
what was found in human terms ("7 conversations, about 25 minutes of talking, mostly 11:40 pm–1 am"),
where the files are, and that they can open `viewer.html` to search and listen. Offer next steps.

## Show your progress as a checklist

Most of this happens out of sight, and a run can take hours, so keep a visible checklist. Before
doing anything else, create one with your to-do or plan tool (Claude Code's task list, Codex's plan
tool); if you have no such tool, post the list as a short message and repeat it, updated, after each
step. Use these items, in the user's words:

1. Find the recordings
2. Check this computer
3. Install the tools (first time only, ~4 GB, 5–15 min)
4. Check the recordings and estimate the time
5. Try a short sample (optional)
6. Process the recordings
7. Check the results
8. Show you where everything is

Once `LS check` has run, put the time estimates into items 5 and 6 ("Try a 10-minute sample (about
6 min)", "Process the recordings (about 50 min)"). Mark one item in progress at a time, and mark it
done as soon as it is. Mark items that don't apply as skipped, or drop them (Install when `doctor`
already passes; the sample if they decline it). While processing, keep item 6's text current from
`progress.log`: the step, the recording, and the time left, for example "Process the recordings:
removing music, 2 of 4, about 30 min left". Add an item when something new needs doing ("Wait for you
to install Homebrew"). Don't put transcript text in the checklist.

## Steps

Below, `LS` means running the bundled package with the environment's Python and
`PYTHONPATH=SKILL_DIR`:

- macOS and Linux: `PYTHONPATH="SKILL_DIR" ~/.loudscribe/venv/bin/python -m loudscribe`
- Windows (PowerShell):
  `$env:PYTHONPATH="SKILL_DIR"; & "$env:USERPROFILE\.loudscribe\venv\Scripts\python.exe" -m loudscribe`

`LS process` exits with 0 when every recording finished, 2 when some failed (they're named at the
end and in `progress.log`; the rest are done), and 1 when none could be processed.

1. **Find the recordings.** Ask where they are if unclear. Check they're readable audio and not still
   copying. One night = one folder; if nights are mixed, ask or group them by date. If they're on the
   recorder or in a cloud folder, copy them to a local folder first (see Rules) and remember where:
   the final list must include those copies.
2. **Look at the computer** (OS, chip/GPU, free disk ≥ 15 GB, memory) and pick the route:

   | Computer | Route |
   |---|---|
   | Apple Silicon, ≥ 16 GB | the tool as is (fast) |
   | Apple Silicon, 8 GB | recommend the sample first, with other apps closed; if music removal crashes or crawls, use `--no-music-removal` |
   | Linux + NVIDIA graphics card | the tool as is, on the GPU (untested); if transcription fails on the GPU, run with `LOUDSCRIBE_BACKEND=cpu` (music removal still uses the GPU) |
   | Windows (with or without a graphics card), or Linux without an NVIDIA card | the tool on the CPU: roughly 2.5–3.5 h per hour of audio. NVIDIA cards aren't used on Windows (the standard PyTorch for Windows is CPU-only): don't promise GPU speed. On Linux, install CPU-only PyTorch first (`pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu`) to avoid a multi-GB CUDA download |
   | Intel Mac | untested: no music removal (current PyTorch has no Intel Mac builds); install Python 3.11 + `faster-whisper`, `torch==2.2.2` and a transformers version that supports it, then run with `--no-music-removal` and `LOUDSCRIBE_BACKEND=cpu`; or transcribe with faster-whisper alone and skip the lyric classifier. Tell the user lyrics may slip through |

3. **Install** (once; ~4 GB, roughly 10–30 minutes depending on the internet connection). If
   `LS doctor` passes, skip to step 4 (`LS doctor --no-music-removal` when the user only has quiet
   recordings).
   - **ffmpeg** on the PATH: macOS `brew install ffmpeg` (Homebrew from https://brew.sh first, which
     the user installs: it needs their password); Linux `sudo apt install ffmpeg` (or dnf/pacman;
     the user runs sudo); Windows `winget install --id Gyan.FFmpeg -e` (the user accepts the terms;
     open a new shell afterwards so PATH updates).
   - **Python 3.11** (3.12 also fine; numpy needs ≥ 3.11): macOS `brew install python@3.11`; Linux:
     whichever of 3.11 or 3.12 the distribution packages, with its venv module (Ubuntu 24.04:
     `sudo apt install python3.12-venv`; Ubuntu 22.04 needs 3.11 from the deadsnakes PPA); Windows
     `winget install --id Python.Python.3.11 -e`.
   - **Environment** (tested versions; it picks mlx-whisper on Apple Silicon and faster-whisper
     elsewhere):
     - macOS and Linux: `python3.11 -m venv ~/.loudscribe/venv` (or `python3.12`), then
       `~/.loudscribe/venv/bin/python -m pip install -r "SKILL_DIR/requirements.txt"`
     - Windows (PowerShell): `py -3.11 -m venv "$env:USERPROFILE\.loudscribe\venv"`, then
       `& "$env:USERPROFILE\.loudscribe\venv\Scripts\python.exe" -m pip install -r "SKILL_DIR\requirements.txt"`
   - **Models** (~3 GB): `LS setup` (add `--no-music-removal` to skip the music-removal model).
   - **Check:** `LS doctor`. It reports the engine it will use (Apple GPU, NVIDIA or CPU).
   - Linux + NVIDIA: faster-whisper needs the cuBLAS/cuDNN libraries that PyTorch installed in the
     environment: add `…/site-packages/nvidia/cublas/lib` and `…/nvidia/cudnn/lib` to
     `LD_LIBRARY_PATH` when running `LS`.
   - If something won't install, fix forward and re-check with `LS doctor`.
4. **Check the recordings and estimate the time:** `LS check "<folder>"` (add `--json` for
   structured output, `--no-music-removal` for quiet places). It lists each recording with its start
   time and length, measures clipping, and estimates the time for the whole night and for a sample.
   Tell the user in plain words:
   - how many recordings and hours of audio, and whether the clock times come from the file names;
   - clipping: over ~5% of samples (overall, or in the worst 5 minutes) means the recorder gain was
     too high there and little conversation will survive; `RECORDER_SETUP.md` explains how to set the
     recorder next time. This is often the most useful thing you tell them;
   - how long the whole night will take, and whether that figure comes from this computer's own
     earlier run or is a typical figure for this kind of computer.
5. **Offer a short sample first.** Ask whether they'd like to try a sample before the full run, giving
   both times, for example: "A 10-minute sample takes about 6 minutes; the whole night about 50
   minutes. Try the sample first?" Recommend it the first time someone uses this and whenever
   something is unusual (new recorder, low memory, CPU-only, another language); on repeat runs offer
   it but suggest going straight to the full run. If they want it:
   - `LS sample "<folder>"`, adding `--at 23:40` to start at a clock time when they remember talking.
     It copies 10 minutes (5 on CPU-only computers) into `~/loudscribe-output/samples/<night>-sample/`,
     named so the clock times stay right, and prints the folder. The original isn't touched.
   - `LS process "<that folder>"`, then give them the sample's
     `recordings/…_music_removed.m4a` and `conversations.m4a` to listen to, and say what was found.
   - Run `LS check "<folder>"` again: the estimate for the whole night now uses the speed measured
     on the sample. Give them the new figure and wait for their OK before the full run.
6. **Process:** `LS process "<folder>"` (`--no-music-removal` for quiet places, `--language xx` if
   not English). Before starting, say how long it will take and that they can leave it running.
   Long runs: keep the laptop plugged in; on macOS prefix `caffeinate -i` (on Windows the tool asks
   Windows to stay awake and off the slow efficiency cores, and says so if Windows refuses: then ask
   the user to set Sleep to Never while plugged in); for runs over an hour start it
   detached (`nohup … &`, or `Start-Process` on Windows) so it survives the session, and follow
   `~/loudscribe-output/<night>/progress.log`. On Windows the venv's `python.exe` is a launcher that
   starts the real Python as a child: to stop a run, stop both. It resumes where it stopped if
   interrupted; re-running with a different `--language` or music removal setting redoes only the
   steps that changes. A file that fails is skipped and named at the end; the rest still finish.
   If a recording file is replaced (or a sample re-cut) under the same name, its old results are
   discarded and it's processed again.
7. **Check the results** before reporting. Each recording's `work/<id>/segments.tsv` shows how many
   lines were kept, dropped (and why) and called lyrics: use its counts and reasons columns, not the
   text (see Rules). If almost nothing was found, look back at the clipping from step 4: heavy
   clipping means the recording is the problem, not the tool. Then say what was found in human
   terms (see "Talking to the user").
8. **Show where everything is.** `LS process` ends by printing the list of files it made and saving
   it as `WHERE_THINGS_ARE.txt` in the results folder: the original recordings, `viewer.html`,
   `transcript.txt`, `conversations.m4a`, each recording's music-removed audio, conversation audio
   and transcript, `progress.log` and the technical files, and where the tools and models live. Show
   the user that list with full paths in their system's format, and add what you made yourself:
   - copies of recordings you made (step 1), and that they can delete them once they're happy;
   - the sample: its copy in `~/loudscribe-output/samples/<night>-sample/` and its results in
     `~/loudscribe-output/<night>-sample/`;
   - the output file of a detached run, if you started one;
   - any recordings that failed, with the reason from `progress.log`.

   Offer to open the results folder or `viewer.html` for them.
9. **Help them use it:** `LS find "words"` (add `--json`) gives the clock time and the
   position in each audio file, and tolerates misheard words. The music-removed recording has the
   same timeline as the original, so a "recording" position is also the position in the original
   file: if they want to hear a moment *with* the music (or as recorded), cut it from the original
   into a new file with ffmpeg (`-ss START -to END`), never editing the original. Summaries and
   feedback need the transcript text (see Rules): lines aren't labelled by speaker and contain
   misheard words, so infer carefully and say when unsure.

For lectures or classes (`--no-music-removal`), people usually want a summary and action items
rather than the audio: offer to write one from the transcript (after the privacy check above).

## When things don't fit

Different recorder, language, format or OS, a failing install, a request the tool doesn't cover: use
your judgement, keep the Rules, keep the tool's tested models and settings, and tell the user in one
sentence what you did differently. The hallucination list and lyric rules were tuned on English
recordings; with other languages, check `segments.tsv` drop reasons on the sample first.

## Updating or removing the skill

- **Update:** `git pull` in `SKILL_DIR` (or download it again from
  https://github.com/kjsanjay/loudscribe-skill if it isn't a git checkout), never while a run is
  going. Then run `LS doctor`: a new version may need `pip install -r requirements.txt` or
  `LS setup` again.
- **Remove:** after the user confirms, delete `SKILL_DIR` and `~/.loudscribe` (the environment,
  music-removal model, temporary files and speed record), and from the Hugging Face cache
  (`~/.cache/huggingface/hub`, or `HF_HUB_CACHE`) only the folders of the models in
  `loudscribe/models.py` (`models--mlx-community--whisper-large-v3-turbo`,
  `models--dropbox-dash--faster-whisper-large-v3-turbo` or `models--mobiuslabsgmbh--…`,
  `models--MIT--ast-finetuned-audioset-10-10-0.4593`). Leave their results (`~/loudscribe-output`),
  any copies of recordings you made, ffmpeg and Python unless they ask: those may be wanted, or used
  by other programs.
