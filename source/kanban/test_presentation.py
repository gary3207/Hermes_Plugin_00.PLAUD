"""Readable dates and expected Highlights absence, without a live account."""
import unittest
import plaud_kanban as job


class PresentationTest(unittest.TestCase):
    def test_detailed_plan_is_separate_from_measured_history(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        report = dict(outcome='failed',phase=4,failure_phase=3,ended='fixture',seconds=8,
                      failed=['IMAGE_WEB_RESPONSE_INVALID'],phases=[dict(phase=3,seconds=7)])
        body = job.card_body(active,report)
        self.assertIn('오류 발생 단계: 3/4',body)
        self.assertIn('최종 도달 단계: 4/4',body)
        self.assertIn('### 상세 처리 순서 (조건부 계획)',body)
        plan = body.split('### 상세 처리 순서 (조건부 계획)',1)[1]
        for text in (plan, job.WAIT_BODY):
            self.assertIn('1. **준비**',text)
            self.assertIn('   - 2.2 ID 형식·중복 검사',text)
            self.assertIn('   - 3.7 Summary 표지 이미지 조회·다운로드·로컬 임베드',text)
            self.assertIn('   - 3.8 안전 검사·원자 저장·파일명 갱신',text)
            self.assertIn('   - 4.2 최종 산출물 소유권·경로·무결성 검증',text)
        self.assertNotIn('| 3.7 |',body)
        self.assertNotIn('미보고',body)
        self.assertEqual(8,report['seconds'])

    def test_terminal_dates_and_normal_highlights_are_separate_from_errors(self):
        active = dict(started='2026-10-22T09:01:53',directory='fixture',run=1,token='fixture')
        report = dict(outcome='failed',phase=4,ended='2026-10-22T09:07:21',seconds=327.418,
                      failed=['APIConnectionError'],verification_errors=['ARTIFACT_MISSING_OR_DUPLICATE'],
                      pending=33,highlights_unavailable=33,other_unavailable=0)
        body = job.card_body(active,report)
        self.assertIn('시작: 2026년 10월 22일 오전 9시 1분 53초',body)
        self.assertIn('종료: 2026년 10월 22일 오전 9시 7분 21초',body)
        self.assertIn('총 소요: 5분 27.418초',body)
        self.assertIn('Highlights 없는 기록: 33개 (정상 생략·실패 아님)',body)
        self.assertNotIn('미제공 자료: 33',body)
        self.assertIn('APIConnectionError, ARTIFACT_MISSING_OR_DUPLICATE',body)

    def test_history_shows_only_recorded_timings_and_keeps_waiting_plan(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        report = dict(outcome='success',phase=4,ended='fixture',seconds=10,
                      phases=[dict(phase=3,seconds=8,ended='fixture',operations=[
                          dict(name='원문 조회',seconds=2,calls=4),
                          dict(name='번역',seconds=3,calls=1)])])
        body = job.card_body(active,report)
        labels = ('Obsidian 기존 노트 인덱스', 'PLAUD 전체 목록 조회', '레코드 원문 조회',
                  '기존 노트 변경·미완료·무결성 검사', '변경 없음이면 번역·저장 생략',
                  '렌더 캐시 검사·재사용', '캐시 미스 시 조건부 번역', '텍스트 렌더링',
                  'Summary 표지 이미지 조회·다운로드·로컬 임베드', '안전 검사·원자 저장·파일명 갱신',
                  '최종 Obsidian 재인덱스', '최종 산출물 소유권·경로·무결성 검증')
        for label in labels:
            self.assertIn(label,job.WAIT_BODY)
        positions = [job.WAIT_BODY.index(label) for label in labels]
        self.assertEqual(sorted(positions),positions)
        self.assertIn('| 3.1 | 레코드 원문 조회 (레코드별 누적) | 0분 02.000초 |',body)
        self.assertIn('| 3.5 | 캐시 미스 시 조건부 번역 (레코드별 누적) | 0분 03.000초 |',body)
        for absent in ('미보고', '기록 없음', '미실행', '| 3.2 |', '| 1/4 |',
                       '기타 레코드 처리 누적', '실행 증거가 아닌 처리 계획'):
            self.assertNotIn(absent,body)
        self.assertIn('상위 단계에 포함',body)
        self.assertEqual(10,report['seconds'])
        self.assertEqual(8,report['phases'][0]['seconds'])

    def test_skipped_unreported_and_zero_call_operations_are_omitted(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        report = dict(outcome='failed',phase=2,ended='fixture',failed=['PLAUD_AUTH_REQUIRED'],
                      phases=[dict(phase=1,seconds=0),dict(phase=2,seconds=1),
                              dict(phase=3,seconds=0,skipped=True),
                              dict(phase=4,seconds=0,unreported=True)])
        body = job.card_body(active,report)
        self.assertIn('| 1/4 | 준비 | 0분 00.000초 |',body)
        self.assertIn('| 2/4 | 목록 조회 | 0분 01.000초 |',body)
        self.assertNotIn('| 3/4 |',body)
        self.assertNotIn('| 4/4 |',body)
        self.assertIn('PLAUD_AUTH_REQUIRED',body)
        report = dict(outcome='success',ended='fixture',phases=[dict(phase=3,seconds=1,operations=[
            dict(name='번역',seconds=0,calls=0)])])
        self.assertNotIn('| 3.5 |',job.card_body(active,report))

    def test_running_phase_is_visible_and_empty_history_has_no_table(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        body = job.card_body(active,dict(phase=1))
        self.assertIn('| 1/4 | 준비 | 진행 중 |',body)
        self.assertNotIn('| 2/4 |',body)
        body = job.card_body(active,dict(outcome='success',ended='fixture'))
        self.assertNotIn('| 단계 |',body)

    def test_noon_midnight_and_unknown_dates(self):
        self.assertIn('오전 12시',job.display_time('2026-10-22T00:00:00'))
        self.assertIn('오후 12시',job.display_time('2026-10-22T12:00:00'))
        self.assertEqual('기록 없음',job.display_time(None))
        self.assertEqual('bad-fixture',job.display_time('bad-fixture'))

    def test_unknown_historical_pending_is_not_assumed_to_be_highlights(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        report = dict(outcome='success',phase=4,ended='fixture',failed=[],pending=3)
        body = job.card_body(active,report)
        self.assertIn('종류 미기록',body)
        self.assertNotIn('Highlights 없는 기록: 3개',body)


if __name__ == '__main__':
    unittest.main()
