"""Readable dates and expected Highlights absence, without a live account."""
import unittest
import plaud_kanban as job


class PresentationTest(unittest.TestCase):
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

    def test_actual_order_is_visible_without_inventing_child_timings(self):
        active = dict(started='fixture',directory='fixture',run=1,token='fixture')
        report = dict(outcome='success',phase=4,ended='fixture',seconds=10,
                      phases=[dict(phase=3,seconds=8,ended='fixture',operations=[
                          dict(name='원문 조회',seconds=2,calls=4),
                          dict(name='번역',seconds=3,calls=1)])])
        body = job.card_body(active,report)
        labels = ('Obsidian 기존 노트 인덱스', 'PLAUD 전체 목록 조회', '레코드 원문 조회',
                  '기존 노트 변경·미완료·무결성 검사', '변경 없음이면 번역·저장 생략',
                  '렌더 캐시 검사·재사용', '캐시 미스 시 조건부 번역', '렌더링·안전 검사·저장',
                  '최종 Obsidian 재인덱스', '최종 산출물 소유권·경로·무결성 검증')
        for text in (body, job.WAIT_BODY):
            for label in labels:
                self.assertIn(label,text)
            positions = [text.index(label) for label in labels]
            self.assertEqual(sorted(positions),positions)
        self.assertIn('| 3.1 | 레코드 원문 조회 (레코드별 누적) | 0분 02.000초 |',body)
        self.assertIn('| 3.5 | 캐시 미스 시 조건부 번역 (레코드별 누적) | 0분 03.000초 |',body)
        self.assertIn('| 3.2 | 기존 노트 변경·미완료·무결성 검사 | 미보고 / 기록 없음 |',body)
        self.assertIn('기타 레코드 처리 누적: 0분 03.000초',body)
        self.assertIn('실행 증거가 아닌 처리 계획',body)
        self.assertEqual(10,report['seconds'])
        self.assertEqual(8,report['phases'][0]['seconds'])

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
