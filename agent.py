"""Daily Europe sponsorship leads. Public feeds, no model fees or applications."""
import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SUBJECT = 'Visa Sponsorship Opportunity in Europe'
NL_REGISTER = 'https://ind.nl/en/public-register-recognised-sponsors/public-register-work'
UK_REGISTER = 'https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers'
ALLOWED = {'api.ashbyhq.com', 'boards-api.greenhouse.io', 'api.smartrecruiters.com', 'ind.nl', 'www.gov.uk', 'assets.publishing.service.gov.uk', 'api.github.com'}

class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, value):
        self.parts.append(value)
    def handle_endtag(self, tag):
        if tag in ('p', 'li', 'div', 'br', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

def plain(value):
    parser = Text()
    parser.feed(html.unescape(value or ''))
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())

def request(url, method='GET', payload=None, authenticated=False):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED:
        raise ValueError('Unapproved endpoint')
    headers = {'User-Agent': 'EuropeJobDigest/1.0', 'Accept': 'application/json,text/html,text/csv'}
    if authenticated:
        if parsed.hostname != 'api.github.com':
            raise ValueError('Credentials may only go to GitHub')
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    for attempt in range(3):
        time.sleep(2 + attempt * 3)
        try:
            req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
            with urllib.request.urlopen(req, timeout=25) as response:
                data = response.read(15_000_001)
            if len(data) > 15_000_000:
                raise ValueError('Response too large')
            return data
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404, 409, 422, 429) or method != 'GET' or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if method != 'GET' or attempt == 2:
                raise

def get_json(url):
    return json.loads(request(url))

def normalized(name):
    name = re.sub(r'\b(b\.?v\.?|n\.?v\.?|limited|ltd\.?|plc|gmbh|inc\.?)\b', '', name.lower())
    return re.sub(r'[^a-z0-9]', '', name)

def registers():
    path = ROOT / 'cache/sponsors.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if time.time() - saved['fetched'] < 7 * 86400:
            return saved
    result = {'fetched': time.time(), 'nl': [], 'uk': [], 'errors': []}
    try:
        page = request(NL_REGISTER).decode('utf-8-sig')
        for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', page, re.S | re.I):
            cells = [plain(v) for v in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, re.S | re.I)]
            if len(cells) >= 2 and re.fullmatch(r'\d{8}', cells[1]):
                result['nl'].append(cells[0])
        if not result['nl']:
            raise ValueError('No sponsor entries parsed')
    except Exception as error:
        result['errors'].append('NL register: ' + type(error).__name__)
    try:
        page = request(UK_REGISTER).decode()
        links = re.findall(r'https://assets\.publishing\.service\.gov\.uk/[^\s"<>]+\.csv', page)
        if not links:
            raise ValueError('Register link unavailable')
        result['uk_url'] = links[0]
        rows = csv.DictReader(io.StringIO(request(links[0]).decode('utf-8-sig')))
        result['uk'] = [row['Organisation Name'] for row in rows if row['Route'].strip() == 'Skilled Worker']
        if not result['uk']:
            raise ValueError('No UK sponsor entries parsed')
    except Exception as error:
        result['errors'].append('UK register: ' + type(error).__name__)
    # Do not cache failures for a week; retry the failed country next run.
    if not result['errors']:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding='utf-8')
    return result

def extract_jobs(kind, board, config):
    if kind == 'ashby':
        entries = get_json('https://api.ashbyhq.com/posting-api/job-board/' + urllib.parse.quote(board, safe='')).get('jobs', [])
        jobs = []
        for entry in entries:
            if entry.get('isListed') is False:
                continue
            address = entry.get('address', {}).get('postalAddress', {})
            jobs.append({'company': board, 'title': entry['title'], 'location': entry.get('location', ''), 'country': address.get('addressCountry', ''), 'published': entry.get('publishedAt'), 'description': entry.get('descriptionPlain') or plain(entry.get('descriptionHtml')), 'url': entry['jobUrl']})
        return jobs, len(entries)
    if kind == 'greenhouse':
        entries = get_json('https://boards-api.greenhouse.io/v1/boards/' + board + '/jobs?content=true').get('jobs', [])
        return [{'company': board, 'title': e['title'], 'location': e.get('location', {}).get('name', ''), 'country': '', 'published': e.get('first_published') or e.get('first_published_at'), 'description': plain(e.get('content', '')), 'url': e['absolute_url']} for e in entries], len(entries)
    jobs, examined = [], 0
    for offset in range(0, 300, 100):
        listing = get_json(f'https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=100&offset={offset}')
        entries = listing.get('content', [])
        examined += len(entries)
        for entry in entries:
            title = entry.get('name', '')
            if not any(relevant(title, c) for c in config['candidates']):
                continue
            detail = get_json(f'https://api.smartrecruiters.com/v1/companies/{board}/postings/{entry["id"]}')
            if detail.get('active') is not True:
                continue
            sections = detail.get('jobAd', {}).get('sections', {})
            location = detail.get('location', {})
            jobs.append({'company': detail.get('company', {}).get('name', board), 'title': title, 'country': location.get('country', ''), 'location': location.get('fullLocation', location.get('city', '')), 'published': detail.get('releasedDate'), 'description': '\n'.join(plain(s.get('text', '')) for s in sections.values()), 'url': detail.get('postingUrl') or detail.get('applyUrl')})
        if not entries or offset + len(entries) >= listing.get('totalFound', 0):
            break
    return jobs, examined

def relevant(title, candidate):
    value = title.lower().replace('-', ' ')
    return any(word in value for word in candidate['titles']) and not re.search(r'\b(intern|student|trainee|director|vice president|head of)\b', value)

COUNTRY_CODES = {'nl': 'Netherlands', 'de': 'Germany', 'ie': 'Ireland', 'dk': 'Denmark', 'se': 'Sweden', 'fi': 'Finland', 'be': 'Belgium', 'lu': 'Luxembourg', 'at': 'Austria', 'fr': 'France', 'gb': 'United Kingdom', 'uk': 'United Kingdom'}
CITIES = {'Amsterdam': 'Netherlands', 'Rotterdam': 'Netherlands', 'Berlin': 'Germany', 'Munich': 'Germany', 'London': 'United Kingdom', 'Cambridge': 'United Kingdom', 'Leeds': 'United Kingdom', 'Dublin': 'Ireland', 'Paris': 'France', 'Copenhagen': 'Denmark', 'Stockholm': 'Sweden', 'Helsinki': 'Finland', 'Brussels': 'Belgium', 'Vienna': 'Austria'}

def country_for(job, config):
    country = COUNTRY_CODES.get(job['country'].lower(), job['country'])
    if country:
        return country if country in config['countries'] else None
    location = job['location']
    found = {country for country in config['countries'] if re.search(r'\b' + re.escape(country) + r'\b', location, re.I)}
    found.update(country for city, country in CITIES.items() if re.search(r'\b' + city + r'\b', location, re.I))
    return next(iter(found)) if len(found) == 1 else None

def assess(job, candidate, config, sponsor_data, today):
    country = country_for(job, config)
    if not country:
        return None, 'outside_or_ambiguous_location'
    description = job['description']
    negative = r'no (?:visa )?(?:sponsorship|support)|(?:cannot|unable to|do not|does not|don.t|not able to) (?:offer|provide|support|sponsor)[^.\n]{0,35}(?:visa|sponsorship)|visa (?:sponsorship|support) (?:is )?(?:not available|not offered|unavailable)|(?:must|need to) (?:already )?(?:have|hold) (?:the )?(?:existing )?(?:right|authori[sz]ation) to work|EU citizens only'
    if re.search(negative, description, re.I):
        return None, 'no_sponsorship_or_existing_work_rights'
    try:
        posted = datetime.fromisoformat((job['published'] or '').replace('Z', '+00:00')).date()
        age = (today - posted).days
        if age < 0 or age > config['max_age_days']:
            return None, 'outside_date_window'
    except ValueError:
        return None, 'posting_date_unverified'
    # Language requirements are only inferred from explicit requirement wording.
    for language in ['Dutch', 'German', 'French', 'Swedish', 'Danish', 'Finnish']:
        requirement = rf'(?:fluent|fluency|proficien\w*|native|business.level)\s+(?:in\s+)?{language}|{language}\s+(?:is\s+)?(?:required|essential|mandatory)|fluent in[^.\n]{{0,35}}\b{language}\b'
        if re.search(requirement, description, re.I) and language not in candidate['languages']:
            return None, 'required_language_unverified'
    support = re.search(r'visa (?:support|assistance|sponsorship)|(?:support|assist)[^.\n]{0,50}(?:visa|work permit)|sponsor[^.\n]{0,35}(?:visa|work permit)', description, re.I)
    evidence = None
    if support:
        start = max(description.rfind('\n', 0, support.start()) + 1, support.start() - 70)
        evidence = {'type': 'Posting mentions visa support; verify candidate-specific eligibility', 'source': job['url'], 'quote': description[start:support.end() + 90].strip()}
    if country in ('Netherlands', 'United Kingdom'):
        register = sponsor_data['nl' if country == 'Netherlands' else 'uk']
        matches = [name for name in register if normalized(name) == normalized(job['company'])]
        if len(matches) == 1:
            evidence = {'type': 'Employer on official register; vacancy sponsorship unconfirmed', 'source': NL_REGISTER if country == 'Netherlands' else UK_REGISTER, 'entity': matches[0]}
    if not evidence:
        return None, 'sponsorship_evidence_missing'
    matched = [skill for skill in candidate['skills'] if re.search(r'(?<!\w)' + re.escape(skill) + r'(?!\w)', description, re.I)]
    # Ranking aid only. This is not must-have coverage or an ATS fit percentage.
    rank = min(100, 40 + 4 * len(matched) + (10 if evidence.get('entity') else 7))
    key = hashlib.sha256((candidate['id'] + '|' + job['url']).encode()).hexdigest()[:20]
    return {**job, 'country': country, 'candidate': candidate['id'], 'key': key, 'rank': rank, 'matched_skills': matched, 'sponsorship': evidence, 'posted_date': posted.isoformat(), 'review_note': 'Potential lead only. Confirm English working language, mandatory skills, salary, route and employer willingness. No application submitted.'}, None

def read_state(local):
    path = ROOT / 'state/latest.json'
    return json.loads(path.read_text()) if path.exists() else {'seen': {}, 'emails': {}}

def save_state(state, local):
    path = ROOT / 'state/latest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')

def send_email(config, report):
    missing = [name for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD') if not os.environ.get(name)]
    if missing:
        return {'status': 'blocked', 'reason': 'Missing GitHub Actions secrets: ' + ', '.join(missing)}
    msg = EmailMessage()
    msg['From'] = os.environ['SMTP_USERNAME']
    msg['To'] = config['recipient']
    msg['Subject'] = SUBJECT
    msg.set_content(report)
    port = int(os.environ.get('SMTP_PORT') or '465')
    if port == 465:
        connection = smtplib.SMTP_SSL(os.environ['SMTP_HOST'], port, context=ssl.create_default_context(), timeout=30)
    elif port == 587:
        connection = smtplib.SMTP(os.environ['SMTP_HOST'], port, timeout=30)
        connection.starttls(context=ssl.create_default_context())
    else:
        raise ValueError('SMTP_PORT must be 465 or 587')
    with connection as smtp:
        smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        if smtp.send_message(msg):
            raise RuntimeError('Recipient refused')
    return {'status': 'accepted_by_smtp', 'note': 'Inbox delivery is not independently confirmed.'}

def render(config, selected, coverage, skipped, today):
    report = [SUBJECT, f'Date: {today} (India time)', '', 'Daily potential leads for both candidates. No applications submitted.', 'Register recognition and visa wording do not guarantee sponsorship, salary-route eligibility or a complete skills match.', '']
    for candidate in config['candidates']:
        leads = [j for j in selected if j['candidate'] == candidate['id']]
        report += [candidate['display_name'] + ' - ' + str(len(leads)) + ' new potential leads', '']
        for job in leads:
            report += [job['company'] + ' - ' + job['title'], job['location'] + ' | Posted: ' + job['posted_date'], 'Resume keywords: ' + (', '.join(job['matched_skills']) or 'Title match only; review carefully'), 'Evidence: ' + job['sponsorship']['type'], 'Evidence link: ' + job['sponsorship']['source'], 'Apply: ' + job['url'], job['review_note'], '']
        if not leads:
            report += ['No new roles passed the date, location, language and sponsorship evidence checks in the configured sources.', '']
    report += ['Source coverage:'] + [f'- {c["source"]}: {c["status"]}; entries checked: {c.get("entries", 0)}' for c in coverage]
    report += ['', 'Exclusions: ' + json.dumps(skipped, sort_keys=True), 'Salary and route thresholds require vacancy-specific verification; no salary eligibility is inferred.', 'English working language must be confirmed before applying. Search coverage is limited to configured public employer boards.', '']
    if os.environ.get('GITHUB_RUN_ID'):
        report += ['Run: https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + os.environ['GITHUB_RUN_ID']]
    return '\n'.join(report)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', action='store_true', help='Local state; never sends email')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text())
    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    state = read_state(args.local)
    sponsors = registers()
    coverage = [{'source': 'Official sponsor registers', 'status': '; '.join(sponsors['errors']) or 'fetched/current weekly cache', 'entries': len(sponsors['nl']) + len(sponsors['uk'])}]
    skipped, selected, evaluated = {}, [], []
    for kind, key in [('ashby', 'ashby_boards'), ('greenhouse', 'greenhouse_boards'), ('smartrecruiters', 'smartrecruiters_companies')]:
        for board in config[key]:
            try:
                jobs, total = extract_jobs(kind, board, config)
                coverage.append({'source': kind + '/' + board, 'status': 'searched (SmartRecruiters cap 300)' if kind == 'smartrecruiters' else 'searched', 'entries': total})
                for job in jobs:
                    for candidate in config['candidates']:
                        if not relevant(job['title'], candidate):
                            continue
                        match, reason = assess(job, candidate, config, sponsors, today)
                        evaluated.append({'candidate': candidate['id'], 'url': job['url'], 'reason': reason or 'potential_lead'})
                        if reason:
                            skipped[reason] = skipped.get(reason, 0) + 1
                        elif match['key'] not in state['seen']:
                            selected.append(match)
            except Exception as error:
                coverage.append({'source': kind + '/' + board, 'status': 'failed: ' + type(error).__name__, 'entries': 0})
    unique = {j['key']: j for j in selected}
    selected = []
    for candidate in config['candidates']:
        ranked = sorted((j for j in unique.values() if j['candidate'] == candidate['id']), key=lambda j: (-j['rank'], j['url']))
        selected += ranked[:config['max_per_candidate']]
    report = render(config, selected, coverage, skipped, today)
    output = ROOT / 'reports'
    output.mkdir(exist_ok=True)
    (output / 'daily-report.txt').write_text(report, encoding='utf-8')
    (output / 'reviewed.json').write_text(json.dumps({'leads': selected, 'coverage': coverage, 'decisions': evaluated}, indent=2), encoding='utf-8')
    email_key = str(today)
    prior = state['emails'].get(email_key, {})
    email = {'status': 'not_sent_local'}
    if not args.local:
        if prior.get('status') in ('attempting', 'ambiguous', 'accepted_by_smtp'):
            email = {'status': 'already_attempted', 'previous_status': prior['status']}
        else:
            missing = any(not os.environ.get(k) for k in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'))
            state['emails'][email_key] = {'status': 'blocked' if missing else 'attempting'}
            save_state(state, False)  # Persist before SMTP; do not auto-retry ambiguous sends.
            try:
                email = send_email(config, report)
            except Exception as error:
                email = {'status': 'ambiguous', 'reason': type(error).__name__}
            state['emails'][email_key] = email
        # Only mark leads delivered after the mail server accepted the message.
        if email['status'] == 'accepted_by_smtp':
            state['seen'].update({j['key']: {'date': str(today), 'url': j['url'], 'candidate': j['candidate']} for j in selected})
    state['last_run'] = {'date': str(today), 'leads': len(selected), 'reviewed': len(evaluated), 'coverage': coverage, 'email': email}
    save_state(state, args.local)
    (output / 'delivery.json').write_text(json.dumps(email, indent=2))
    summary = report + '\n\nEmail status: ' + json.dumps(email)
    (output / 'summary.md').write_text(summary, encoding='utf-8')
    print(json.dumps({'leads': len(selected), 'reviewed': len(evaluated), 'email': email}))
    if not args.local and email['status'] not in ('accepted_by_smtp', 'already_attempted'):
        raise SystemExit(2)
    if all(c['status'].startswith('failed') for c in coverage[1:]):
        raise SystemExit('All job feeds failed; report is incomplete.')

if __name__ == '__main__':
    main()

