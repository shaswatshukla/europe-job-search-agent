import copy
import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import agent

class DigestChecks(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).parent / 'config.example.json').read_text())
        self.candidate = self.config['candidates'][1]
        self.job = {'company': 'Example', 'title': 'Senior QA Engineer', 'country': 'Germany', 'location': 'Berlin', 'published': '2026-10-15T10:00:00Z', 'description': 'English. Python pytest SQL automation. A relocation package includes visa support.', 'url': 'https://example.com/jobs/qa'}
        self.register = {'nl': [], 'uk': []}

    def assess(self, job=None, today=date(2026, 10, 16)):
        return agent.assess(job or self.job, self.candidate, self.config, self.register, today)

    def test_explicit_support_lead_has_evidence_not_submission(self):
        job, reason = self.assess()
        self.assertIsNone(reason)
        self.assertEqual(job['sponsorship']['source'], self.job['url'])
        self.assertIn('No application submitted', job['review_note'])

    def test_negative_sponsorship_overrides_positive_boilerplate(self):
        for phrase in ['No sponsorship', 'We cannot sponsor visas', 'We do not provide visa support', 'Visa sponsorship is not available', 'You must already have the right to work', 'EU citizens only']:
            with self.subTest(phrase=phrase):
                job = {**self.job, 'description': self.job['description'] + '\n' + phrase}
                self.assertEqual(self.assess(job)[1], 'no_sponsorship_or_existing_work_rights')

    def test_unknown_date_does_not_use_update_time(self):
        job = {**self.job, 'published': None, 'updated_at': '2026-10-16'}
        self.assertEqual(self.assess(job)[1], 'posting_date_unverified')

    def test_old_and_future_dates_excluded(self):
        for published in ['2026-09-24', '2026-10-17']:
            self.assertEqual(self.assess({**self.job, 'published': published})[1], 'outside_date_window')

    def test_exactly_21_days_allowed(self):
        self.assertIsNone(self.assess({**self.job, 'published': '2026-09-25'})[1])

    def test_india_remote_is_not_europe(self):
        job = {**self.job, 'country': 'India', 'location': 'Remote; Berlin team'}
        self.assertEqual(self.assess(job)[1], 'outside_or_ambiguous_location')

    def test_europe_generic_location_unverified(self):
        self.assertEqual(self.assess({**self.job, 'country': '', 'location': 'Europe, Remote'})[1], 'outside_or_ambiguous_location')

    def test_required_french_excluded(self):
        job = {**self.job, 'description': self.job['description'] + '\nFluent in French and English.'}
        self.assertEqual(self.assess(job)[1], 'required_language_unverified')

    def test_sponsorship_question_alone_is_not_support(self):
        job = {**self.job, 'description': 'Do you require sponsorship? Python QA.'}
        self.assertEqual(self.assess(job)[1], 'sponsorship_evidence_missing')

    def test_register_match_is_conditional(self):
        self.register['uk'] = ['Example Limited']
        job = {**self.job, 'country': 'gb', 'location': 'London', 'description': 'Python pytest QA.'}
        match, reason = self.assess(job)
        self.assertIsNone(reason)
        self.assertIn('unconfirmed', match['sponsorship']['type'])

    def test_similar_name_does_not_inherit_sponsor(self):
        self.register['uk'] = ['Example Finance Limited']
        job = {**self.job, 'country': 'gb', 'description': 'Python pytest QA.'}
        self.assertEqual(self.assess(job)[1], 'sponsorship_evidence_missing')

    def test_candidate_specific_dedup_keys(self):
        first = self.assess()[0]
        second = agent.assess(self.job, self.config['candidates'][0], self.config, self.register, date(2026, 10, 16))[0]
        self.assertNotEqual(first['key'], second['key'])

    def test_missing_email_credentials_block_without_network(self):
        with patch.dict(os.environ, {}, clear=True), patch('agent.smtplib.SMTP_SSL') as smtp:
            result = agent.send_email(self.config, 'report')
            self.assertEqual(result['status'], 'blocked')
            smtp.assert_not_called()

    def test_email_recipient_and_exact_subject(self):
        env = {'SMTP_HOST': 'smtp.gmail.com', 'SMTP_USERNAME': 'sender@example.com', 'SMTP_PASSWORD': 'test-only'}
        with patch.dict(os.environ, env, clear=False), patch('agent.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            self.assertEqual(agent.send_email(self.config, 'report')['status'], 'accepted_by_smtp')
            msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(msg['To'], 'candidate@example.com')
            self.assertEqual(msg['Subject'], 'Visa Sponsorship Opportunity in Europe')

    def test_no_credentials_to_external_hosts(self):
        with self.assertRaises(ValueError):
            agent.request('https://ind.nl/en/test', authenticated=True)
        with self.assertRaises(ValueError):
            agent.request('http://api.github.com/test')

    def test_report_explicitly_labels_no_matches_and_failures(self):
        report = agent.render(self.config, [], [{'source': 'TestFeed', 'status': 'failed: TimeoutError'}], {}, date(2026, 10, 16))
        self.assertIn('No new roles passed', report)
        self.assertIn('TimeoutError', report)
        self.assertIn('No applications submitted', report)

if __name__ == '__main__':
    unittest.main()

