# Music Download & Sort Pipeline — Agent Capability Report

> **Purpose**: Document the reasoning, tools, and techniques used by the AI agent to download
> a list of songs from a screenshot and sort them into themed folders. This serves as a
> reference for building equivalent capabilities into the Odysseus harness.

## 1. Task Overview

**Input**: A screenshot (PNG) of a checklist titled "Stáhnout písničky:" (Czech for "Download songs:")
containing 16 items — a mix of specific songs, artist names, vague references, and one non-music item.

**Output**: MP3 files downloaded via `yt-dlp` and sorted into genre-themed subdirectories under
`/home/bubakulus/Music/downloads_sorted/`.

---

## 2. Pipeline Stages

### Stage 1: Image → Structured List (Vision / OCR)

**Tool used**: Multimodal LLM vision (built-in to the agent model)

**What happened**:
- The agent received the screenshot as a PNG image attachment.
- The LLM's vision capability extracted the text list directly — no separate OCR tool was needed.
- This is the simplest stage but one of the most critical: the agent had to correctly read
  handwritten/typed text including accented characters, ambiguous names, and parenthetical notes.

**Extracted list**:
| # | Raw Text from Screenshot |
|---|--------------------------|
| 1 | Chamber of reflection - mac demarco |
| 2 | Joe dart and olllam |
| 3 | Fleshless hand |
| 4 | Ethan French music |
| 5 | Thievery corporation - I have only Lebanon blonde and sweet tides, but I would like much more of their music |
| 6 | Virado - (Spanish?) band |
| 7 | Marina Herlop jaque |
| 8 | Six blade nights |
| 9 | Echoes of the Roman Ruins - jesper Kyd |
| 10 | Kitschkrieg, gut genung mit blumengarten |
| 11 | Kokiri forest - Zelda lofi |
| 12 | Art of sathish |
| 13 | Door kickers: Action squad release trailer music |
| 14 | Sunshine over the counter. Madison cunningham |
| 15 | She goes - thisisgirlgroup |
| 16 | The figs - their songs |

**Harness capability needed**:
- Multimodal input (image → text extraction)
- Alternatively: an OCR pipeline (e.g., Tesseract, PaddleOCR, or cloud Vision API)

---

### Stage 2: Disambiguation & Research (Web Search)

**Tool used**: `search_web` (web search tool) via parallel subagents

**What happened**:
The raw list items are ambiguous. The agent needed to resolve:

| Problem Type | Example | Resolution |
|---|---|---|
| Vague artist name | "Fleshless hand" | → ML Buch – "Fleshless hand" (Danish dream pop artist) |
| Mondegreen / typo | "Six blade nights" | → Dire Straits – "Six Blade Knife" (common mishearing) |
| Not music at all | "Art of sathish" | → Digital art YouTube channel, NOT a musician. Skipped. |
| Artist, not a song | "Ethan French music" | → Australian multi-instrumentalist. Downloaded top tracks. |
| Partial info | "Kitschkrieg, gut genung mit blumengarten" | → KitschKrieg & Blumengarten – "Gut Genug" (German alt-pop collab) |
| "More of their music" | "Thievery Corporation" | User already has 2 songs, wants more → downloaded 5 additional tracks |
| "Their songs" | "The figs - their songs" | → Indie folk duo, downloaded 3 representative tracks |
| Game soundtrack | "Door kickers: Action squad release trailer music" | → Found the game's release trailer on YouTube |
| Zelda remix | "Kokiri forest - Zelda lofi" | → Mikel & GameChops lofi remix of the Zelda OoT track |

**Technique**: Two parallel research subagents (8 items each) searched the web simultaneously.
Each subagent was given the raw text and asked to return:
1. Correct artist name
2. Best YouTube URL (official audio/video preferred)
3. Genre/style classification
4. Brief notes

**Harness capability needed**:
- Web search API (Google Search, Brave Search, SerpAPI, etc.)
- Parallel execution (fan-out pattern)
- Entity resolution / fuzzy matching logic
- Knowledge of music domain (or access to music databases: MusicBrainz, Discogs, Last.fm API)

---

### Stage 3: Genre Classification & Folder Structure

**Tool used**: LLM reasoning (no external tool)

**What happened**:
Based on the genre information from Stage 2, the agent clustered the 15 music items
(excluding Art of Sathish) into 9 themed folders:

```
downloads_sorted/
├── indie-and-dream-pop/         # Mac DeMarco, ML Buch, Girl Group
├── jazz-funk-and-neo-soul/      # Joe Dart & Olllam, Ethan French
├── downtempo-and-chill/         # Thievery Corporation (×5), Kokiri Forest lofi
├── experimental/                # Marina Herlop
├── latin-and-world-folk/        # VIRADO (×3)
├── rock-and-blues/              # Dire Straits
├── soundtracks-and-cinematic/   # Jesper Kyd, Door Kickers trailer
├── german-alt-pop/              # KitschKrieg & Blumengarten
└── folk-and-singer-songwriter/  # Madison Cunningham, The Figs (×3)
```

**Clustering logic**:
- Primary: Genre similarity (e.g., downtempo + lofi → same folder)
- Secondary: Mood/vibe similarity (e.g., Thievery Corporation and Zelda lofi are both "chill")
- Constraint: Avoid too many single-item folders (merge where sensible)

**Harness capability needed**:
- Genre taxonomy / ontology (can use Last.fm tags, MusicBrainz genres, or Spotify's genre seeds)
- Clustering algorithm (or just LLM-based classification with a predefined taxonomy)
- Configurable granularity (how many folders? merge threshold?)

---

### Stage 4: Download Execution

**Tool used**: `yt-dlp` (command-line YouTube downloader)

**What happened**:
A bash script was generated and executed that:

1. Created the folder structure with `mkdir -p`
2. For each song, ran `yt-dlp` with YouTube search:
   ```bash
   yt-dlp "ytsearch1:<search query>" \
       -x --audio-format mp3 \
       --no-progress \
       --max-filesize 255.5M \
       --no-playlist \
       -o "<themed_folder>/%(title)s.%(ext)s"
   ```

**Key `yt-dlp` flags explained**:

| Flag | Purpose |
|---|---|
| `ytsearch1:<query>` | Searches YouTube and returns the **first result** matching the query |
| `-x` | Extract audio only (no video) |
| `--audio-format mp3` | Convert extracted audio to MP3 |
| `--no-progress` | Suppress progress bar (cleaner logs) |
| `--max-filesize 255.5M` | Skip files larger than 255.5 MB |
| `--no-playlist` | Don't expand playlists, just download the single video |
| `-o "path/%(title)s.%(ext)s"` | Output template using the video title as filename |

**Why `ytsearch1:` instead of direct URLs**:
- Research subagents may return hallucinated or outdated YouTube URLs
- `ytsearch1:` is more robust — it performs a live YouTube search at download time
- Trade-off: may occasionally pick the wrong video (e.g., a cover instead of the original)

**Harness capability needed**:
- `yt-dlp` binary (or equivalent: `youtube-dl`, Invidious API, Piped API)
- Search-based download (`ytsearch:` prefix)
- Audio extraction + format conversion (requires `ffmpeg`)
- Error handling and retry logic
- Output template configuration

---

### Stage 5: Existing Script Analysis

**Tool used**: `view_file` (file reader)

**What happened**:
Before writing the download script, the agent examined the user's existing
`/home/bubakulus/Music/run_py.sh` and `download_yt_dlp_script.py` to understand:

- The existing download workflow (Zenity GUI → yt-dlp)
- The output template format: `%(fulltitle)s - %(artist)s.%(ext)s`
- The retry logic (individual video ID downloading with retry loop)
- The folder structure conventions

The existing script uses **Zenity** (GUI dialog tool) for user interaction, making it
unsuitable for headless/automated batch use. The agent therefore wrote a new non-interactive
bash script.

**Harness capability needed**:
- Code analysis / understanding of existing tools
- Ability to adapt existing patterns rather than starting from scratch

---

## 3. Tools & APIs Summary

| Tool / API | Used For | Stage |
|---|---|---|
| **LLM Vision** (multimodal) | Extract text from screenshot | 1 |
| **Web Search** (`search_web`) | Disambiguate artist/song names, find genres | 2 |
| **LLM Reasoning** | Cluster songs into themed folders | 3 |
| **`yt-dlp`** (CLI) | Download audio from YouTube | 4 |
| **`ffmpeg`** (via yt-dlp) | Convert audio to MP3 | 4 |
| **`view_file`** | Analyze existing download scripts | 5 |
| **Subagent System** | Parallelize research across 2 batches | 2 |
| **File System Tools** | Create folders, write scripts | 3-4 |

---

## 4. Architecture Diagram

```
┌──────────────┐
│  Screenshot  │
│   (PNG)      │
└──────┬───────┘
       │ Vision/OCR
       ▼
┌──────────────┐
│  Raw Text    │
│  List (16    │
│  items)      │
└──────┬───────┘
       │ Fan-out to parallel research agents
       ▼
┌──────────────────────────────────────┐
│  Web Search & Disambiguation         │
│  ┌─────────┐  ┌─────────┐           │
│  │ Agent 1 │  │ Agent 2 │           │
│  │ (8 items)│  │ (8 items)│          │
│  └────┬────┘  └────┬────┘           │
│       │             │                │
│       ▼             ▼                │
│  Structured results:                 │
│  - Artist name                       │
│  - Song title                        │
│  - YouTube URL / search query        │
│  - Genre classification              │
│  - Notes (non-music flagged)         │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────┐
│  Genre Clustering    │
│  (LLM reasoning)     │
│  → 9 themed folders  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────┐
│  Batch Download Script   │
│  (bash + yt-dlp)         │
│                          │
│  for each song:          │
│    yt-dlp ytsearch1:...  │
│    → themed_folder/      │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────┐
│  MP3 files sorted    │
│  in themed folders   │
└──────────────────────┘
```

---

## 5. Edge Cases & Decisions

| Decision | Rationale |
|---|---|
| **"Art of Sathish" skipped** | Web search revealed it's a digital concept art channel, not music |
| **"Six blade nights" → "Six Blade Knife"** | Recognized as a common mondegreen for the Dire Straits track |
| **Thievery Corporation: 5 extra tracks** | User said "I would like much more of their music" — agent selected 5 well-known tracks beyond the 2 they already have |
| **The Figs: 3 tracks** | User said "their songs" — agent picked 3 representative tracks |
| **Ethan French: 3 tracks** | User said "music" (general) — agent picked 3 popular singles |
| **VIRADO: 3 tracks** | User listed as a band — agent picked 3 of their known repertoire songs |
| **`ytsearch1:` over URLs** | More reliable than potentially hallucinated YouTube URLs from LLM research |
| **Non-interactive script** | User's existing script uses Zenity GUI; batch operation needs headless execution |

---

## 6. Reproducing This in Odysseus

To build equivalent capability into your harness, you need these components:

### Minimum Viable Pipeline
1. **OCR / Vision module** — Extract text from images (Tesseract, PaddleOCR, or multimodal LLM)
2. **Music metadata resolver** — Map vague text to `(artist, title, genre)` tuples
   - Options: MusicBrainz API, Last.fm API, Discogs API, or web search + LLM parsing
3. **Genre classifier** — Assign genre labels and cluster into folders
   - Options: Last.fm tags, Spotify genre seeds, or LLM-based classification
4. **YouTube search + download** — `yt-dlp` with `ytsearch:` prefix
5. **Error handling** — Log failures, retry on transient errors, skip gracefully

### Nice-to-Have Enhancements
- **Spotify/Apple Music cross-reference** — Verify song identity against streaming catalogs
- **Audio fingerprinting** — Verify downloaded audio matches expected song (AcoustID/Chromaprint)
- **Duplicate detection** — Check if user already has the song in their library
- **User confirmation step** — Show resolved list before downloading ("Did you mean X?")
- **Playlist support** — Download entire playlists/albums when user says "their songs" or "more music"

---

## 7. Folder Structure Created

```
/home/bubakulus/Music/downloads_sorted/
├── indie-and-dream-pop/
│   ├── Mac DeMarco - Chamber of Reflection
│   ├── ML Buch - Fleshless Hand
│   └── Girl Group - She Goes
├── jazz-funk-and-neo-soul/
│   ├── the olllam ft. Joe Dart - The Vision
│   ├── Ethan French - sight of me
│   ├── Ethan French - fake it til you make it
│   └── Ethan French - reckless and arranged
├── downtempo-and-chill/
│   ├── Thievery Corporation - The Richest Man in Babylon
│   ├── Thievery Corporation - Saudade
│   ├── Thievery Corporation - Until the Morning
│   ├── Thievery Corporation - Heaven's Gonna Burn Your Eyes
│   ├── Thievery Corporation - Culture of Fear
│   └── Mikel & GameChops - Kokiri Forest (Zelda lofi)
├── experimental/
│   └── Marina Herlop - Jaque
├── latin-and-world-folk/
│   ├── VIRADO - Sabor a Mí
│   ├── VIRADO - Vendaval
│   └── VIRADO - Porque Yo Te Amo
├── rock-and-blues/
│   └── Dire Straits - Six Blade Knife
├── soundtracks-and-cinematic/
│   ├── Jesper Kyd - Echoes of the Roman Ruins
│   └── Door Kickers: Action Squad - Trailer Music
├── german-alt-pop/
│   └── KitschKrieg & Blumengarten - Gut Genug
└── folk-and-singer-songwriter/
    ├── Madison Cunningham - Sunshine Over the Counter
    ├── The Figs - Death of Me
    ├── The Figs - East of Eden
    └── The Figs - Colorado
```

**Total: 25 downloads across 9 themed folders** (1 item skipped: Art of Sathish = not music)
