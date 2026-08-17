# Lumos — Architecture & Planning Document

> Personal knowledge capture tool

**Version:** 0.4
**Date:** 2026-03-07

---

## 1. Overview

Lumos는 웹 하이라이트, 북마크, 이미지를 캡처하고 JSONL에 저장하는 순수 데이터 도구다.

```
  INPUT                            STORAGE                    OUTPUT
  ─────                            ───────                    ──────
  Chrome Extension ──┐
  Kindle Import    ──┼──▶  items.jsonl + media/ + cache/  ──▶  CLI (lumos)
  Diigo Import     ──┘
```

**Lumos가 하는 것:** 캡처, 저장, 검색, priority 관리, 페이지 캐시, 이미지 OCR
**Lumos가 하지 않는 것:** Discord, Anki, LLM — 외부 도구가 JSONL을 직접 읽으면 됨

---

## 2. Design Principles

1. **One file** — `items.jsonl` 하나. 이미지는 `media/`, 캐시는 `cache/`.
2. **One record type** — 북마크, 하이라이트, 이미지 모두 같은 스키마.
3. **Future-proof** — JSONL. 10년 뒤에도 `grep`으로 읽을 수 있다.
4. **Offline-first** — 인터넷 없이 동작. 클라우드는 sync 수단일 뿐.
5. **Minimal** — 상시 서버 없음. CLI + Chrome Extension + Native Messaging Host.

---

## 3. Data

### 3.1 Directory Structure

```
~/.config/lumos.json                 # 모든 설정 (data_dir 포함)

<data_dir>/                          # default: ~/.lumos/ or config의 data_dir
├── items.jsonl                      # 모든 데이터
├── media/                           # 이미지 파일
└── cache/                           # 페이지 캐시
    ├── {id}.mhtml                   #   원본 페이지 (MHTML)
    ├── {id}.txt                     #   본문 추출 (Readability)
    └── suggest/{url_hash}.json      #   하이라이트 제안 + dismiss 기록
```

### 3.2 Record Schema

```jsonl
{
  "id": "lm_20260307_a1b2c3",
  "type": "page" | "highlight" | "image",
  "url": "https://example.com/article",
  "title": "Article Title",
  "text": "Highlighted text (null for page-only)",
  "note": "User note (optional)",
  "media": "media/img_20260307_a1b2c3.png",
  "ocr_text": "OCR extracted text (async, null until done)",
  "cache": {
    "mhtml": "cache/lm_xxx.mhtml",
    "readable": "cache/lm_xxx.txt"
  },
  "source": {
    "via": "web" | "kindle",
    "book": "Book Title",
    "author": "Author Name",
    "page": 42,
    "location": "1234-1256",
    "original_html": "<a href='...'>text</a>"
  },
  "location": {
    "xpath": "/html/body/div[2]/p[3]",
    "start_offset": 42,
    "end_offset": 78,
    "text_fingerprint": "sha256_first8"
  },
  "priority": 0,
  "created_at": "2026-03-07T14:30:00Z",
  "updated_at": "2026-03-07T14:30:00Z"
}
```

필드는 해당 type에서 불필요하면 `null`. 예: `media`는 `type=image`에서만, `cache`는 `type=page`에서만, `location`은 Chrome Extension 하이라이트에서만.

| 필드 | 설명 |
|------|------|
| `id` | `lm_{date}_{random6}` |
| `type` | `page`, `highlight`, `image` |
| `text` | 하이라이트 텍스트. page-only면 `null` |
| `ocr_text` | Upstage OCR 결과. 비동기 처리, 완료 전까지 `null` |
| `cache` | MHTML + Readability 경로. Cmd+D 저장 시 생성 |
| `source.via` | `web` 또는 `kindle`. Diigo import도 `web` |
| `priority` | 하이라이트/이미지 단위. `type=page`에서는 항상 0 |

**수정/삭제:** 전체 읽기 → 수정 → `os.replace()`로 atomic rewrite.
개인 규모(~10K)에서 ~100ms. 10만 이상이면 SQLite 캐시 고려.

### 3.3 Import Mapping

**Diigo** (JSONL → items.jsonl):

| Diigo | → Lumos | Transform |
|-------|---------|-----------|
| `url` | `url` | as-is |
| `title` | `title` | as-is |
| `created_at` | `created_at` | `"2026/03/06 02:50:18 +0000"` → ISO 8601 |
| `annotations[].content` | `text` | strip HTML → `text`, 원본 → `source.original_html` |
| `annotations[].comments` | `note` | join if multiple |
| (all) | `source.via` | `"web"` |

**Kindle** (My Clippings.txt → items.jsonl):

| Clippings | → Lumos | Transform |
|-----------|---------|-----------|
| Book Title | `title` | as-is |
| Author | `source.author` | 괄호 안에서 추출 |
| page / Location | `source.page`, `source.location` | int / string |
| Highlighted text | `text` | as-is |
| (all) | `url` | `kindle://book/{title_slug}` |
| (all) | `source.via` | `"kindle"` |

---

## 4. CLI (`lumos`)

### 4.1 Commands

```
Usage: lumos [query] [options]

Options:
  --source web|kindle         Filter by source
  --since DATE                After date (e.g. 7d, 2026-01-01)
  --until DATE                Before date
  --limit N                   Page size (default: 10)
  --sort date|priority|title  Sort key (default: date)
  --desc/--asc                Sort direction (default: desc)
  --in title,text,note,ocr    Search scope (default: all, comma-separated)
  --case-sensitive            Case-sensitive search (default: off)

Commands:
  lumos init [--data-dir PATH]      Initial setup
  lumos config show|set|edit|path   Manage config
  lumos add <url> [options]         Add manually
  lumos import diigo <file>         Import Diigo export
  lumos import kindle <file>        Import Kindle clippings
  lumos ocr-retry                   Retry OCR for failed images
```

### 4.2 Interactive UI

Textual TUI. wcwidth로 CJK 정렬. title 컬럼 가변 (source/date 고정).

**기본 (collapsed):**

```
──────────────────────────────────────────────────────────────────────────────
  #  title                                             source    date
──────────────────────────────────────────────────────────────────────────────
  1  IPO 속도 내는 업스테이지, 'SBVA 출신' 진윤정 CFO로 영입
                                                       web       2026-03-06
  2  한국 미국 세금보고 원스탑으로 도와주실 수 있는 회계사분 계실까요?
                                                       web       2026-03-05
▌ 3  Glove80 Review - The ergonomic king               web       2026-03-04 ▐
  4  Glove80 Key Switches - MoErgo                     web       2026-03-04
  5  REALM'25                                          web       2026-03-03
  6  Thinking, Fast and Slow                           kindle    2026-01-15
  7  Atomic Habits                                     kindle    2025-12-20
──────────────────────────────────────────────────────────────────────────────
 j/k ↕  J/K page  enter expand  e expand all  x del  / search  esc quit
```

`▌` `▐` = 선택 행 + 배경색 반전. 첫 줄에만 표시, 텍스트 안 밀림.
짧은 title은 source/date와 같은 줄. 긴 title은 wrap 후 source/date가 다음 줄 컬럼 위치에 정렬.

**enter 또는 번호 → 해당 항목 expand/collapse 토글:**

```
  3  Glove80 Review - The ergonomic king               web       2026-03-04
  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
▌ · The only thing preventing me from giving the Glove80 a perfect score is the build quality and hardware ▐
    customization.
    Priority: 2

  · 📷 ~/Google Drive/Lumos/media/img_20260304_x1y2z3.png
    ┌─────────────────────┐
    │  (imgcat / sixel)   │
    └─────────────────────┘
    build quality 이슈 사진
    Priority: 0

  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  4  Glove80 Key Switches - MoErgo                     web       2026-03-04
──────────────────────────────────────────────────────────────────────────────
 j/k ↕  J/K page  enter expand  e expand all  x del  / search  +- priority  n note  esc quit
```

하이라이트는 터미널 전체 폭 사용. 점선(`┄`)으로 구분. 원본 줄바꿈/불릿/enum 보존.
`j`/`k`로 페이지↔하이라이트 자유 이동. `+`/`-`는 하이라이트에서만 동작 (연타 가능, 제한 없음).
하이라이트 행일 때 footer 뒤에 `+- priority  n note` append.

**하이라이트 많을 때 (kindle 등):**

```
  6  Thinking, Fast and Slow                           kindle    2026-01-15
  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  · Nothing in life is as important as you think it is, while you are thinking about it.
    Priority: 1 | Page: 402

  · The confidence that individuals have in their beliefs depends mostly on the quality of the story they
    can tell about what they see, even if they see little.
    Priority: 0 | Page: 209

  · A reliable way to make people believe in falsehoods is frequent repetition, because familiarity is not
    easily distinguished from truth.
    Priority: 0 | Page: 62

  12 more (enter for next 5)

  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  7  Atomic Habits                                     kindle    2025-12-20
```

기본 3개 표시. enter로 5개씩 추가. `e`로 전체 expand/collapse 토글 (동일 페이징 적용).

**메타데이터:** 하이라이트 아래 한 줄, dim color. 없는 필드 생략.
`Priority: N | Page: N | Location: N | Note: ...`

**검색:**

```
$ lumos "repetition" --in text

 🔍 "repetition" in text, 2 pages
──────────────────────────────────────────────────────────────────────────────
▌ 1  Thinking, Fast and Slow                           kindle    2026-01-15 ▐
     · ...frequent repetition, because familiarity is not easily distinguished from truth.
  2  Atomic Habits                                     kindle    2025-12-20
     · ...repetition is crucial. Each time you repeat an action...
──────────────────────────────────────────────────────────────────────────────
```

매칭 스니펫을 `·`로 미리보기. 이미지는 `text`(캡션) + `note` + `ocr_text`로 검색.

**삭제:** `x` → 확인 프롬프트. 페이지에서 `x` → 소속 하이라이트/이미지 + cache/media 파일 함께.

### 4.3 Key Bindings

| 키 | 페이지 행 | 하이라이트 행 |
|---|---|---|
| `j` / `k` | 커서 이동 | 하이라이트 간 이동 |
| `enter` | expand/collapse 토글 | 더 보기 (5개씩) |
| `{N}` | N번 바로 expand | — |
| `e` | 전체 expand/collapse | 전체 expand/collapse |
| `+` / `-` | — | priority up/down (연타) |
| `x` | 페이지 + 소속 삭제 | 해당 하이라이트만 삭제 |
| `n` | — | 노트 편집 |
| `/` | 검색 | 검색 |
| `J` / `K` | pagination | pagination |
| `Esc` / `q` | 종료 | 종료 |

---

## 5. Chrome Extension

### 5.1 Features

**텍스트 하이라이트 (Content Script)**
- `mouseup`으로 선택 감지 → mini-toolbar (💡 Highlight / 📝 Note)
- `<mark>` 태그를 Range 양 끝에 삽입 (링크 안전 — `surroundContents` 대신 text node 개별 wrap)
- XPath + text offset + text_fingerprint로 위치 저장
- 재방문 시 복원: XPath → fingerprint → text search 순 fallback

**이미지 저장 (Content Script)**
- 우클릭 → context menu "Save to Lumos"
- fetch → blob → Native Host → `media/` 저장
- 비동기로 Upstage OCR → `ocr_text` 업데이트. 실패 시 자동 재시도 (max 3, exponential backoff)

**하이라이트 제안 (Content Script + LLM)**
- 페이지 로드 시 본문을 whitespace-normalized 텍스트 + text node 인덱스로 평탄화
- Native Host → LLM: "독자가 밑줄 긋고 나중에 인용할 문장". 요약이 아니라 킨들·리디북스의
  '가장 많이 하이라이트한 구절' 쪽에 가깝다. 개인화 없음 — 글의 내용이 기준
- 프롬프트는 규칙 4개(verbatim / 완전한 문장 / 파트당 최대 1개 / 없으면 건너뛰기)만 유지.
  측정 결과 규칙 7개와 밀도·길이·분포가 동일했고, 겹침·개수는 `verify_phrases`가 코드로
  강제한다. 단 규칙을 전부 빼면 모델이 문장 대신 용어(~30자)를 뽑아 하이라이트가 아니게 된다
- **분산은 요청이 아니라 구조로 강제한다.** "전체에 고루 분산" 같은 문장은 긴 글에서
  통하지 않는다 — 모델은 앞부분만 읽고 할당량을 채우고 멈춰서 제안이 도입부에 몰린다.
  본문을 제안 개수만큼의 파트(`=== PART n/N ===`, 문단→문장→공백 순으로 경계 선택)로
  잘라 넣고 파트당 최대 1개를 요청한다. 실측: 12,000자 글에서 7%/22%/99% 3개 → 8~88% 6개
- 돌아온 응답도 같은 예산으로 검증한다 — 본문을 제안 개수만큼의 구간으로 나눠 구간당
  1개까지만 채택, 빈 구간이 남으면 탈락분으로 개수를 채운다 (분산 우선, 개수는 유지)
- `max_chars`(기본 12,000)는 **자르는 위치가 아니라 호출 1회의 읽기 예산**이다. 앞에서부터
  12,000자만 보내면 긴 글은 물리적으로 앞 1/4에만 제안이 생긴다 — 저장된 페이지 13개 중
  10개가 12,000자를 넘는다. 긴 글은 `max_calls`(기본 6)까지 **병렬 호출로 나눠 전문을**
  읽고, 그보다 더 길면 그때부터 파트별로 예산을 나눠 발췌한다. 50,000자 글 5초/5회 호출
- LLM 응답은 본문에 verbatim으로 존재하는 것만 통과 (환각 방지). 겹치는 구절 제거.
  파트 마커가 섞여 들어온 구절은 마커만 제거하고 검증 — 경계에 걸친 인용도 살린다
- 제안 개수는 고정 상한이 아니라 **본문 길이 비례** — `ratio`(기본 6%) ÷ `phrase_chars`.
  이건 목표가 아니라 상한이고, 밑줄 그을 게 없는 파트는 모델이 건너뛴다. `max_phrases`(80)는
  20만자 페이지용 폭주 방지선일 뿐이다. 실측: 8천자 → 2~3개, 2.4만자 → 9개, 5만자 → 19개
  (각각 상한 3/10/20). 링크 목록뿐인 페이지는 거의 비고, 에세이는 끝까지 채워진다
- 모델은 길이에 따라 갈아끼울 수 있다 (`llm.fast_model` + `suggest.fast_below`). 다만 기본은
  꺼둔다 — 같은 기사를 6/8/10/12천자로 잘라 3회씩 측정한 결과 `solar-mini`는 개수는 동급이고
  1~3초 빠르지만 **첫 제안이 본문 16~27%에서 시작**한다(pro4는 5~11%, 3회 모두 동일 패턴).
  1.5만자부터는 편차가 커지고(3~6개), 5만자에서는 verbatim 통과율이 65~80%까지 떨어진다.
  제안은 페이지당 1회 호출 + 캐싱이라 몇 초는 한 번만 지불되므로 품질 쪽을 택했다
- 연노랑(`#FFF9C4`)으로 표시. **읽기 전용** — 핸들러 없음. 하이라이트는 사용자가 직접 선택해서 저장
- URL별로 `cache/suggest/{hash}.json`에 캐싱 — 같은 페이지는 LLM을 다시 부르지 않는다.
  제안 로직을 바꿨는데 결과가 그대로면 이 캐시를 지우거나 `refresh`로 다시 요청해야 한다
- 제안은 DOM에만 존재. `items.jsonl`은 사용자가 직접 저장한 것만 담는다
- SPA(Reddit 등) 대응: `chrome.tabs.onUpdated`의 `changeInfo.url` → content script 재실행
- 본문이 아직 렌더 안 됐으면(client-rendered) 1.5s 간격 3회 재시도

> Chrome은 native host를 shell 환경 없이 spawn한다. `.zshrc`의 `export`는 보이지 않으므로
> host가 시작 시 `~/.env`를 직접 로드한다 (`config.load_env()`). OCR도 동일하게 적용.

**페이지 저장 + 캐시 (Service Worker)**
- `Cmd+D` / `Ctrl+D` → 저장 + 아이콘 전환
- MHTML: `chrome.pageCapture.saveAsMHTML()` → 원본 보존 (JS-proof)
- Readability: 본문 추출 → 검색용
- 캐시 방식: config `cache.mode` (`both` | `mhtml` | `readable` | `none`)

### 5.2 URL Index

매 페이지마다 Native Host spawn 방지를 위해 `chrome.storage.local`에 URL→item_ids 맵 캐싱.

```
[Extension 시작] → Native Host: get_url_index → chrome.storage.local에 캐싱
[페이지 로드]    → chrome.storage.local 조회 (<1ms) → hit이면 Native Host로 상세 조회 → 복원
[저장]           → Native Host: save_* → chrome.storage.local index 로컬 갱신
```

---

## 6. Native Messaging Host

Chrome이 필요할 때만 spawn, 작업 끝나면 종료. 상시 서버 아님.

**macOS 등록:** `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.lumos.host.json`
`lumos init`이 자동 생성.

**Protocol:**

| Action | 용도 | 반환 |
|--------|------|------|
| `save_highlight` | 텍스트 하이라이트 | item |
| `save_image` | 이미지 저장 + OCR 시작 | item |
| `save_page` | Cmd+D + 캐시 생성 | item |
| `get_url_index` | Extension 시작 시 1회 | `{url: [ids]}` |
| `get_items_by_ids` | 하이라이트 복원 | items |
| `suggest_highlights` | 페이지 로드 시 제안 (캐시 우선) | `{phrases, color}` |

---

## 7. Project Structure

```
lumos/
├── pyproject.toml
├── src/lumos/
│   ├── core/
│   │   ├── models.py          # Pydantic: Item, Source, Location
│   │   ├── store.py           # JSONL read/write/search/update
│   │   ├── config.py          # config 해석
│   │   └── media.py           # 이미지/캐시 경로
│   ├── cli/
│   │   ├── main.py            # typer app
│   │   ├── interactive.py     # Textual TUI
│   │   └── import_cmd.py      # import diigo|kindle
│   └── native_host/
│       └── host.py
└── tests/
```

`pip install -e .` → `lumos` 명령어 등록.
Native Host: `python -m lumos.native_host.host`

---

## 8. Configuration

모든 설정은 `~/.config/lumos.json` 하나.

**`lumos init`:**
```
$ lumos init --data-dir ~/Google\ Drive/Lumos

✅ Created: ~/Google Drive/Lumos/{items.jsonl, media/, cache/}
✅ Config: ~/.config/lumos.json
✅ Native Host registered
✅ Ready!
```

**`~/.config/lumos.json`:**
```json
{
  "data_dir": "~/Google Drive/Lumos",
  "extension": {
    "shortcut_save_page": "Ctrl+D",
    "highlight_color": "#FFEB3B",
    "mini_toolbar": true
  },
  "cache": {
    "mode": "both"
  },
  "ocr": {
    "enabled": true,
    "provider": "upstage",
    "api_key_env": "UPSTAGE_API_KEY",
    "retry_max": 3
  },
  "list": {
    "default_limit": 10
  }
}
```

| key | 값 | 설명 |
|---|---|---|
| `data_dir` | path | 데이터 디렉토리. 미설정 시 `~/.lumos/` |
| `cache.mode` | `both` \| `mhtml` \| `readable` \| `none` | 페이지 캐시 방식 |
| `ocr.enabled` | bool | 이미지 저장 시 OCR 자동 실행 |
| `ocr.api_key_env` | string | API key를 읽을 환경변수 이름 |
| `ocr.retry_max` | int | OCR 실패 시 자동 재시도 횟수 |
| `list.default_limit` | int | `lumos` 기본 표시 개수 |

`LUMOS_DATA_DIR` 환경변수로 `data_dir` 오버라이드 가능.

---

## 9. Development Phases

### Phase 1: Core + CLI (Week 1-2)
- [ ] `lumos.core`: Pydantic Item 모델, JSONL store, config 해석
- [ ] `lumos init`, `lumos config`
- [ ] `lumos import diigo`, `lumos import kindle`
- [ ] `lumos add`
- [ ] `lumos [query]` (인터랙티브 TUI)
- [ ] `lumos ocr-retry`
- [ ] Native Messaging Host

### Phase 2: Chrome Extension (Week 3-4)
- [ ] 텍스트 하이라이트 + 링크 안전 + 복원
- [ ] 이미지 저장 + 비동기 OCR
- [ ] Cmd+D 페이지 저장 + 캐시 (MHTML + Readability)
- [ ] URL index (`chrome.storage.local`)
- [ ] 아이콘 상태 + 팝업 UI

### Phase 3: Polish (Week 5-6)
- [ ] 팝업 내 검색
- [ ] OCR 재시도 (exponential backoff)
- [ ] Edge case + 테스트

---

## 10. Resolved Decisions

| 결정 | 결과 |
|------|------|
| 데이터 구조 | 단일 `items.jsonl` + `media/` + `cache/` |
| page/highlight | 통합. 같은 스키마, `type`으로 구분 |
| search/list | 통합. `lumos [query]` 하나 |
| 태그 | 제거 |
| hidden | 제거. `x` = 삭제 |
| Priority | 하이라이트/이미지 단위만 |
| source | `web` 또는 `kindle` |
| config | `~/.config/lumos.json` 하나. `LUMOS_DATA_DIR`로 오버라이드 |
| URL 조회 | `chrome.storage.local` index |
| 페이지 캐시 | MHTML + Readability. config로 변경 가능 |
| OCR | Upstage. 비동기 + 자동 재시도 |
| 검색 | case-insensitive. `--in` 범위 제한. semantic search 추후 |