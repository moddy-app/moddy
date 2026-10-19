# 2026-10-19 — Utility tools: finishing and integrating the 8 lots

## What was done
- The 8 lots of the utility toolbox (plan: `docs/archive/UTILITY_TOOLS_PLAN.md`)
  were finished by one agent each, in parallel worktrees, continuing the WIP
  code, then merged into `claude/youthful-cray-l7qj4b`:
  A text (/define /tldr /regex /diff /textstats), B security (/pwned /password
  /uuid /hash /jwt /color), C1 domains & links (/whois /dns /ssl /subdomains
  /link-check + "Check links"), C2 lookups (/status /company /username), D1 media
  (/image /convert, u2netp background removal, CloudConvert provider), D2
  inspection (/exif /sauce + "Find source"), E music links (module + /music),
  F translate button (module).
- Integration: locale JSONs merged by a 3-way key union; gateway/config,
  gateway/__init__, executor, db/base quota seeds and requirements merged keeping
  both sides (duplicate `pypdf` pin removed).
- Fix: `tools/color.py` crashed when Pillow had already cached a named colour as
  a tuple in `ImageColor.colormap` (only visible in the full suite).
- Card review: `source_line` now reads "A, B & C"; /status no longer prints an
  empty "updated"; /tldr reuses `TEXT` like /summarize (empty `SUMMARIZE`
  removed); plural-safe redirect count in the link summary; /define licence line
  no longer repeats the Wiktionary name; unused `NOTION` logo removed.
- Docs: CLAUDE.md (structure + index), API_GATEWAY.md (quotas, call types,
  provider limits, env vars), RAILWAY.md (keys + onnxruntime/py3langid memory),
  EMOJIS.md (tools icons).

## Decisions
- `/tldr` icon: the plan's reuse of `TEXT` wins over a new empty constant.
- VirusTotal: wired but its free tier forbids commercial use — documented in
  API_GATEWAY.md and RAILWAY.md; the owner decides.

## Known issues / follow-ups
- Nothing was run against live Discord or the real third-party APIs (fakes only).
  To test on Railway: CloudConvert (`CLOUDCONVERT_SANDBOX=true`), Spotify page
  access from Railway's IP, Discord metadata stripping for /exif, crt.sh.
- onnxruntime stays mapped (~+95 MB) after the model is unloaded; a short-lived
  child process would reclaim it.
- Command names in the 27 non-runtime locales are agent translations.
- `docs/guides/PERSISTENT_VIEWS.md` not yet updated with the tools' DynamicItems
  (they are listed in each TOOLS_*.md and covered by test_persistent_views).
- Icons: every empty constant in the UTILITY TOOLS block awaits artwork.
