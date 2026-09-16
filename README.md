# 원고의 바깥

`automated-novel`은 기존 `automated-serial-novel`과 별개의 작품/연재 저장소다.

## 작품 핵심

독자가 읽는 텍스트와 이야기 내부의 인물이 작성하고 있는 원고는 동일하다.
세계, 인물, 시대, 작가의 정체와 원고가 현실에 미치는 관계는 1화부터 새로 발견한다.

## 생성 엔진

생성은 다음 순서로 진행된다.

1. 현재 canon과 연재 이력을 읽는다.
2. 다음 화의 여러 서사 방향을 만든다.
3. 장편 지속성, 연속성, 메타 구조를 기준으로 방향을 선택한다.
4. 선택한 방향에서 화 설계도를 만든다.
5. 본문을 생성한다.
6. 메타픽션 구조, 연속성, 서사 품질을 검증한다.
7. 실패하면 검증 피드백을 반영하여 다시 생성한다.
8. 검증을 통과한 뒤에만 상태 delta를 추출하고 canon에 반영한다.
9. 회차 Markdown과 `episodes.json`을 GitHub Pages에 게시한다.

특히 `inner_novel_equals_outer_novel`은 단순한 문체 지침이 아니라 생성/검증 과정에서 유지해야 하는 핵심 불변식이다.

## GitHub Actions 설정

현재 생성 단계에서 필요한 것은 Gemini뿐이다.

### Secret

- `GEMINI_API_KEY`: Google Gemini API 키

### Variable (선택)

- `GEMINI_MODEL`: 특정 Gemini 모델을 강제로 사용할 때만 설정한다. 비워두면 API에서 사용 가능한 생성 모델을 탐색한다.

Novelpia 업로드 관련 Secret/Variable은 생성 엔진이 안정화된 뒤 별도로 추가한다.

## GitHub Pages

`docs/**`가 `main`에 반영되면 `deploy-pages.yml`이 GitHub Pages를 배포한다. 메인 페이지는 `docs/index.html`, 회차 데이터는 `docs/episodes.json`, 실제 본문은 `docs/episodes/*.md`에서 제공한다.

<!-- generation-engine-test -->
