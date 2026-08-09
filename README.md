# IP Sentinel

중소기업의 작업공간(GitHub·Google Drive) 변경사항을 지속 추적해 IP 리스크를 조기 발견하는 AI 에이전트.

## 구조

```
ip-sentinel/
├── main.py                      # Cloud Run 엔트리포인트, GitHub 웹훅 수신 (Orchestrator 역할)
├── agents/
│   ├── context_extraction.py    # diff/문서 → 핵심 기술요소 추출
│   ├── patent_search.py         # KIPRIS Plus 검색 (도구 호출)
│   ├── risk_assessment.py       # Gemini로 위험도(상/중/하) + 근거 + 권장 액션 판단
│   ├── reporter.py              # 최종 마크다운 리포트 생성
│   └── pipeline.py              # 위 4개를 SequentialAgent로 연결
├── services/
│   ├── github_client.py         # 웹훅 서명 검증, 커밋/PR diff 조회
│   ├── kipris_client.py         # KIPRIS Plus REST API 클라이언트
│   └── firestore_store.py       # 리포트 이력 저장/조회
└── tests/
    └── test_kipris_client.py
```

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
- `GITHUB_ACCESS_TOKEN`, `GITHUB_WEBHOOK_SECRET`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`: Vertex AI용

## 확인 필요 (첫 실행 시)

`services/kipris_client.py`의 `KNOWN_FIELDS`는 이 개발 환경에서 KIPRIS Plus 포털에
접근할 수 없어 실제 응답 XML로 검증하지 못했습니다. 처음 실행해서 실제 특허가
검색되면:
1. `search_patents()` 결과의 `raw_fields`를 로그로 확인
2. 필드명이 다르면 `KNOWN_FIELDS` 딕셔너리 값 수정
3. 확인된 실제 응답 예시를 `tests/test_kipris_client.py`의 `SAMPLE_XML`에 반영

## 배포 (Cloud Run)

```bash
gcloud builds submit --tag gcr.io/$GOOGLE_CLOUD_PROJECT/ip-sentinel
gcloud run deploy ip-sentinel \
  --image gcr.io/$GOOGLE_CLOUD_PROJECT/ip-sentinel \
  --region asia-northeast3 \
  --set-secrets KIPRIS_PLUS_SERVICE_KEY=kipris-service-key:latest,GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest,GITHUB_ACCESS_TOKEN=github-access-token:latest
```

배포 후 GitHub 저장소 Settings → Webhooks에 `https://<CLOUD_RUN_URL>/webhook/github`을
`push` 이벤트로 등록하세요 (Secret은 위 `GITHUB_WEBHOOK_SECRET`과 동일하게).

## 아직 안 됨 (TODO)

- Google Drive 연동 (F-03, Should) — `main.py`에 TODO 표시됨
- 오픈소스 라이선스 검사 (F-04, Should) — deps.dev 연동 필요
- 리포트 이력 조회/내보내기 API (F-05, Could) — `firestore_store.list_reports()`는
  구현돼 있으나 이를 노출하는 엔드포인트는 아직 없음
