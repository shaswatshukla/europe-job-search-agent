# Europe job-search agent

Find potential visa sponsorship leads from public Ashby, Greenhouse and SmartRecruiters employer feeds. Checks dates, countries, explicit language restrictions and sponsorship wording, with Netherlands and UK sponsor registers as supporting evidence.

Python 3.12+, standard library only. Copy `config.example.json` to `config.json` and replace the example profiles and recipient. Run `python agent.py --local` for discovery without email. Run `python -m unittest discover -v` for tests. `python agent.py` attempts email if SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD are provided; review your recipient first. SMTP_PORT supports encrypted ports 465 and 587.

Private candidate configuration, reports, state and register caches remain local and ignored by Git. Public CI runs tests only. No resumes, credentials or personal job history are bundled. Your existing private runtime keeps its original schedule; no duplicate daily schedule is enabled here.

These are potential leads, not guaranteed sponsorship or complete skills matches. A sponsor licence does not prove vacancy sponsorship. No automatic applications are submitted. Salary, work eligibility and the employer's willingness require review.
