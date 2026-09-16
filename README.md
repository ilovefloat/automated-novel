# 원고의 바깥

## 생성 엔진

- `scripts/generate_episode.py`: 회차 생성 및 검증
- `scripts/narrative_control.py`: 연속성·반복 제어
- `state/story_state.json`: 작품 상태와 canon
- `docs/episodes/*.md`: 회차 본문
- `docs/episodes.json`: GitHub Pages용 회차 인덱스

생성 흐름:

`방향 후보 → 방향 선택 → 회차 설계 → 본문 생성 → 메타/연속성/품질 검증 → 상태 반영 → 게시`

핵심 불변식: **독자가 읽는 텍스트와 작품 내부 인물이 작성하는 원고는 동일한 텍스트다.**

검증에 실패한 회차는 상태에 반영하거나 게시하지 않는다.

## GitHub Actions

필요한 설정:

- Secret: `GEMINI_API_KEY`
- Variable: `GEMINI_MODEL` (선택)

`Generate episode` workflow가 회차를 생성하고 `docs/`를 갱신한다.

## GitHub Pages

`main`의 `docs/` 변경을 `deploy-pages.yml`이 배포한다.
