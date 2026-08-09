"""services.kipris_client의 XML 파싱 로직 테스트.

실제 KIPRIS Plus 엔드포인트는 이 환경에서 접근할 수 없어(네트워크 미허용),
문서에 나온 통상적인 필드명 구조를 흉내 낸 샘플 XML로 파싱 로직만 검증한다.
실제 서비스키로 처음 호출한 뒤에는, 진짜 응답을 이 파일에 fixture로 추가해
KNOWN_FIELDS가 맞는지 다시 검증할 것을 권장.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.kipris_client import _parse_items, _first_present, KNOWN_FIELDS

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <successYN>Y</successYN>
    <resultCode>00</resultCode>
  </header>
  <body>
    <items>
      <item>
        <applicationNumber>1020200012345</applicationNumber>
        <inventionTitle>잠금화면 광고 모듈을 이용한 광고 시스템 및 방법</inventionTitle>
        <applicantName>버즈빌</applicantName>
        <astrtCont>본 발명은 모바일 단말기의 잠금화면에 광고를 노출하는 시스템에 관한 것으로...</astrtCont>
        <registerStatus>등록</registerStatus>
      </item>
      <item>
        <applicationNumber>1020210067890</applicationNumber>
        <inventionTitle>알림 우선순위 결정을 위한 스코어링 방법</inventionTitle>
        <applicantName>테스트기업</applicantName>
        <astrtCont>사용자 행동 데이터를 기반으로 알림의 우선순위를 산출하는 방법에 관한 것으로...</astrtCont>
        <registerStatus>공개</registerStatus>
      </item>
    </items>
    <count>2</count>
  </body>
</response>
"""


def test_parse_items_returns_two_entries():
    items = _parse_items(SAMPLE_XML)
    assert len(items) == 2


def test_first_present_maps_known_field():
    items = _parse_items(SAMPLE_XML)
    title = _first_present(items[0], KNOWN_FIELDS["title"])
    assert title == "잠금화면 광고 모듈을 이용한 광고 시스템 및 방법"


def test_first_present_returns_none_for_missing_field():
    items = _parse_items(SAMPLE_XML)
    # 샘플에 없는 필드 후보를 넣었을 때 None을 반환하는지 확인
    missing = _first_present(items[0], ["nonExistentField"])
    assert missing is None


if __name__ == "__main__":
    test_parse_items_returns_two_entries()
    test_first_present_maps_known_field()
    test_first_present_returns_none_for_missing_field()
    print("모든 테스트 통과")
