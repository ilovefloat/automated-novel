# 원고의 바깥

`automated-novel`은 기존 `automated-serial-novel`과 별개의 작품/연재 저장소다.

## 작품 핵심

독자가 읽는 텍스트와 이야기 내부의 인물이 작성하고 있는 원고는 동일하다.
세계, 인물, 시대, 작가의 정체와 원고가 현실에 미치는 관계는 1화부터 새로 발견한다.

## GitHub Actions 설정

### Secrets

필수:

- `GEMINI_API_KEY`: Google Gemini API 키
- `NOVELPIA_AUTH_STATE_B64`: 노벨피아 로그인 세션의 Playwright storage state JSON을 base64로 인코딩한 값

선택:

- `GH_SECRET_UPDATE_TOKEN`: 게시 후 새 노벨피아 세션을 `NOVELPIA_AUTH_STATE_B64` Secret으로 갱신하기 위한 GitHub 토큰. 자동 세션 갱신을 사용하지 않으면 등록하지 않아도 된다.

### Variables

필수:

- `NOVELPIA_EDITOR_URL`: **새로 만든 「원고의 바깥」 작품의 작성 페이지 URL**. 기존 작품의 `/mynovel/all/write/...` URL을 사용하면 안 된다.
- `NOVELPIA_PUBLISH_ENABLED`: 처음에는 `false`로 두고 preview가 정상 동작하는 것을 확인한 뒤 `true`로 변경한다.

선택:

- `GEMINI_MODEL`: 특정 Gemini 모델을 강제로 사용할 때만 설정한다. 비워두면 사용 가능한 Flash 계열 모델을 탐색한다.

## 최초 설정 순서

1. Novelpia에서 **새 작품**을 만들고 제목을 `원고의 바깥`으로 설정한다.
2. 새 작품의 작성 페이지 URL을 `NOVELPIA_EDITOR_URL`에 등록한다.
3. Playwright storage state를 새 계정/세션으로 생성하여 `NOVELPIA_AUTH_STATE_B64`에 등록한다.
4. `NOVELPIA_PUBLISH_ENABLED=false` 상태에서 수동 `publish-novelpia` workflow를 preview로 실행한다.
5. 제목과 본문이 새 작품의 편집기에 정확히 들어가는 것을 확인한다.
6. 문제가 없으면 `NOVELPIA_PUBLISH_ENABLED=true`로 바꾼다.
7. 자동 생성 workflow를 실행한다.

API 키와 인증 세션은 저장소 파일에 직접 커밋하지 않는다.
