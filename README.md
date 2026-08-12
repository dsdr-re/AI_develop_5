# IP DETECDOG

전담 특허팀이 없는 스타트업 개발팀의 GitHub 저장소 변경사항을 지속 추적해
특허 침해·오픈소스 라이선스 리스크를 조기 발견하는 AI 에이전트.

## 구조
'''
AI_develop_5/
├── main.py # Cloud Run 엔트리포인트, 웹 UI + GitHub 웹훅 수신 (Orchestrator)
├── agents/
│ ├── context_extraction.py # diff → 핵심 기술요소 추출
│ ├── patent_search.py # KIPRIS Plus 특허 검색 (도구 호출)
│ ├── risk_assessment.py # Gemini로 위험도(상/중/하) + 근거 + 검토 포인트 판단
│ ├── reporter.py # 최종 마크다운 리포트 생성
│ └── pipeline.py # 위 4개를 SequentialAgent로 연결
├── services/
│ ├── github_client.py # 웹훅 서명 검증, 커밋 diff 조회, 커밋 코멘트 게시, 웹훅 등록/삭제
│ ├── kipris_client.py # KIPRIS Plus REST API 클라이언트
│ ├── license_client.py # deps.dev + PyPI classifiers로 requirements.txt 라이선스 조회
│ ├── license_knowledge.py # 임베딩 기반 라이선스 의무사항 RAG 검색
│ ├── secret_store.py # 워크스페이스별 GitHub PAT을 Secret Manager에 저장/조회
│ └── firestore_store.py # 리포트 이력·연결된 저장소 저장/조회
├── knowledge/licenses/ # GPL·AGPL·LGPL·MPL·CPL·EUPL·OSL·SSPL 라이선스 의무사항 근거 문서
├── static/logo.png
└── tests/
└── test_kipris_client.py
'''

## 웹 화면

- `/` — 대시보드 (최근 리포트 요약)
- `/reports` — 리포트 이력 (날짜별 그룹, KST 표시)
- `/reports/{id}` — 리포트 상세 (위험도 배지, 검토 포인트, 해결/재오픈 처리)
- `/connect` — 저장소 연결 (GitHub PAT 입력 → 웹훅 자동 등록, 초기 파일 스캔, 연결 해제)

## 멀티테넌시

저장소를 연결할 때 사용자가 직접 발급한 GitHub PAT을 입력받아 저장소별로
Secret Manager에 저장한다(`workspace-{owner}-{repo}-github-token`). 웹훅
등록/삭제, diff 조회, 커밋 코멘트, 초기 스캔 전부 해당 저장소의 토큰을 조회해서
쓴다. `secret_name`이 없는 레거시 연결은 `GITHUB_ACCESS_TOKEN` 환경변수로
자동 폴백해 기존 서비스가 깨지지 않는다.

## 로컬 실행

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 값 채우기
python main.py
```

## 환경변수

`.env.example` 참고. 특히:
- `KIPRIS_PLUS_SERVICE_KEY`: https://plus.kipris.or.kr 에서 발급받은 키
- `GITHUB_ACCESS_TOKEN`, `GITHUB_WEBHOOK_SECRET`: 레거시 연결용 폴백
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`: Vertex AI용
- `FIRESTORE_COLLECTION`, `FIRESTORE_CONNECTED_REPOS_COLLECTION`: 컬렉션 이름 분리용(선택)

시크릿은 `printf '%s' | gcloud secrets versions add`로 등록할 것 — stdin으로
등록하면 트레일링 뉴라인이 섞여 "Illegal header value" 에러가 난다.

## 배포 (Cloud Run)

```bash
gcloud builds submit --tag gcr.io/$GOOGLE_CLOUD_PROJECT/ip-detecdog
gcloud run deploy ip-detecdog \
  --image gcr.io/$GOOGLE_CLOUD_PROJECT/ip-detecdog \
  --region asia-northeast3 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=asia-northeast3,GOOGLE_GENAI_USE_VERTEXAI=true,GEMINI_MODEL=gemini-2.5-flash,FIRESTORE_COLLECTION=ip_detecdog_reports,FIRESTORE_CONNECTED_REPOS_COLLECTION=ip_detecdog_connected_repos \
  --set-secrets KIPRIS_PLUS_SERVICE_KEY=kipris-service-key:latest,GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest,GITHUB_ACCESS_TOKEN=github-access-token:latest
```

배포 후 GitHub 저장소 Settings → Webhooks에 `https://<CLOUD_RUN_URL>/webhook/github`을
`push` 이벤트로 등록하세요 (Secret은 위 `GITHUB_WEBHOOK_SECRET`과 동일하게). `/connect` 화면에서
PAT을 입력해 자동 등록하는 것도 가능합니다.

## 향후 계획 (TODO)

- Google Drive 연동 — 문서 기반 IP 리스크 탐지 확장
- GitHub OAuth App 전환 — PAT 직접 입력 대신 "GitHub로 연결" 버튼 한 번으로 인가
- diff 크기에 따른 파이프라인 스킵/캐싱 — 현재는 push마다 전체 재실행
- 중간/높음 위험도 판정 기준 구체화
