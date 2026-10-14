# loudscribe

An AI-agent skill (for Claude Code or Codex) that takes long recordings made in loud places
(clubs, bars, festivals) and gives you just the conversations:

1. **Removes the music** from the audio (with a downloaded AI model).
2. **Finds where people are talking**, ignoring song lyrics and noise.
3. **Cuts out those stretches** and joins them into one audio file of the conversations (pauses
   between lines stay in, so the file is longer than the talking, and you may hear the odd snatch
   of music or someone else nearby).
4. **Writes a transcript** of every conversation, with the time it happened and where it is in that
   audio file.
5. **Lets you find any moment:** search what was said and click a line to hear it.

Everything runs on your own computer. Your audio is never uploaded.

**This is an early test release.** It has been tested on Apple Silicon Macs and one Windows laptop.
Feedback is welcome (see [Feedback](#feedback)).

## What you need

- Claude Code or Codex.
- About 15 GB of free disk space and 8 GB of memory (16 GB is more comfortable).

| Computer | Time for 1 hour of audio |
|---|---|
| Mac with Apple Silicon (M1 or newer) | 20–40 min (tested) |
| Windows, with or without a graphics card | about 2.5–3.5 hours: it runs on the processor, as NVIDIA cards aren't used on Windows yet (tested on a laptop) |
| Linux with an NVIDIA graphics card | expected to be fast (not yet tested) |
| Linux without an NVIDIA card | about 2.5–3.5 hours, on the processor (not yet tested) |
| Intel Mac | only without music removal (not yet tested) |

## Install the skill (once)

Ask your agent (Claude Code or Codex):

> Install the loudscribe skill from https://github.com/kjsanjay/loudscribe-skill into my skills
> folder, in a folder named loudscribe.

Restart the agent if it asks you to. No GitHub account is needed.

To update it later, ask: *"Update the loudscribe skill."*

## Use it

Put each night's recordings in their own folder, then ask in plain words, for example:

- *"Clean up and transcribe my recordings in ~/Recordings/night2"*
- *"When did we talk about the after-party?"*
- *"Summarise last night's conversations"*

The agent shows a checklist of what it's doing. The first time, it installs what it needs (about
4 GB; roughly 10–30 minutes depending on your internet connection) and asks you to do anything
that needs your password or approval. It then checks
your recordings, tells you how long processing will take, and offers to try a short sample first
(10 minutes of audio, 5 on slower computers) so you can hear the result before the full run. You can
leave the full run going; if it stops, ask the agent to continue and it picks up where it left off.

At the end it lists where everything is. Results go to `~/loudscribe-output/<folder name>/`:

- `viewer.html`: double-click to search what was said and click a line to hear it
- `transcript.txt`: every conversation, with clock times
- `only_conversations.m4a`: all the conversations joined, music removed
- `recordings/`: each recording with the music removed, its conversations, and its transcript
- `WHERE_THINGS_ARE.txt`: the full list, including logs and where the tools are installed
- `technical/`: logs and data the agent uses; you don't need to open it

If files are named after their start time (as the Tonfarb A20 does, e.g. `20260131223000.WAV`),
transcripts show real clock times.

## Before you record

Recorder settings matter more than anything the software does: see
[RECORDER_SETUP.md](RECORDER_SETUP.md), with settings for the Tonfarb A20.

## Privacy

Processing stays on your computer. When the agent reads transcript text, for example to answer
questions or summarise, that text goes to Anthropic or OpenAI like anything else you share with it.
The skill tells it to ask you first. The recordings include other people's conversations, so be
thoughtful about what you ask for.

Recording people may need their consent, and the rules differ by country and state: in the US, most
states need only one person in the conversation to agree, but some (California, for example) need
everyone's. Check the law where you record; this isn't legal advice.

## Feedback

Problems, rough edges and ideas for what it should do next are all welcome: open an issue on this
repo (**Issues → New issue**) and pick **Test feedback** or **Idea**. Your agent can draft it for
you: ask it to *"draft a loudscribe issue about …"*.

**Never post recordings, transcripts, names, or anything someone said.** Issues are public.

## Remove it

Ask your agent: *"Remove the loudscribe skill and the tools and models it installed."* Your results
in `~/loudscribe-output` stay unless you ask for them to go too.

## License

[PolyForm Noncommercial 1.0.0](LICENSE): free for personal use, study, hobby projects, and for
charities, schools, public research and government bodies; not for commercial use. For commercial
use, ask the author.

The models the skill downloads are made by others and come under their own licenses. Check them
before any use beyond personal:

| Model | Used for | Downloaded from | License (as stated by its publisher) |
|---|---|---|---|
| Whisper large-v3 turbo (OpenAI) | transcription | [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo), converted copies [mlx-community](https://huggingface.co/mlx-community/whisper-large-v3-turbo) (Apple Silicon) and [dropbox-dash](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) (elsewhere) | MIT |
| MelBand-RoFormer vocals model (KimberleyJSN) | music removal | `vocals_mel_band_roformer.ckpt` via the [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) package; original at [KimberleyJSN/melbandroformer](https://huggingface.co/KimberleyJSN/melbandroformer) | MIT |
| AST, AudioSet fine-tuned (MIT CSAIL) | telling speech from singing | [MIT/ast-finetuned-audioset-10-10-0.4593](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593) | BSD-3-Clause |
