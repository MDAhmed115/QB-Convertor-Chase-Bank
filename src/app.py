import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import re, csv, os, hashlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MONTH_NUM = {
    'January': '01', 'February': '02', 'March': '03', 'April': '04',
    'May': '05', 'June': '06', 'July': '07', 'August': '08',
    'September': '09', 'October': '10', 'November': '11', 'December': '12',
}

PAYEE_NORMALISE = {
    'paymentech':        'Paymentech',
    'american express':  'American Express',
    'amex':              'American Express',
    'remote online deposit': 'Remote Online Deposit',
    'remote deposit':    'Remote Online Deposit',
}

DEPOSIT_EXACT = {'deposit'}

ACCOUNT_MAP = {
    'Paymentech':           'Cash in Hand:CC Clearing',
    'American Express':     'Cash in Hand:CC Clearing',
    'Deposit':              'Cash in Hand:Cash Clearing',
    'Remote Online Deposit':'Cash in Hand:Cash Clearing',
}

def normalise_payee(raw: str, ttype: str = '') -> str:
    lower = raw.lower()
    for fragment, name in PAYEE_NORMALISE.items():
        if fragment in lower:
            if name == 'American Express' and ttype == 'deposit':
                return 'American Express Settlement'
            return name
    return raw

def get_account(payee: str) -> str:
    return ACCOUNT_MAP.get(payee, '')

# ─────────────────────────────────────────────
#  CORE: PDF PARSER
# ─────────────────────────────────────────────
def clean_name(desc, ttype=''):
    desc = desc.strip()
    m = re.search(r'Orig CO Name:([A-Za-z0-9&\-\., ]+?)(?:\s+Orig ID:|\s{2,}|$)', desc)
    if m:
        name = re.sub(r'\s+\d{7,}$', '', m.group(1).strip())
        if name.lower() == 'american express' and ttype == 'deposit':
            name = 'American Express Settlement'
        return name
    m = re.search(r'Online Transfer (From|To) Chk \.\.\.(\d+)', desc)
    if m: return f"Online Transfer {m.group(1)} Chk ...{m.group(2)}"
    m = re.search(r'Payment To Chase Card Ending IN (\d+)', desc)
    if m: return f"Chase Card Ending {m.group(1)}"
    m = re.search(r'Online ACH Payment \d+ To (\S+)', desc)
    if m: return m.group(1)
    return desc[:50]

def clean_name_atm(desc):
    """
    Parse a payee name from an ATM/Debit Card Withdrawals section row.
    These rows look like:
      Card Purchase        12/30 Henry Schein* Melville NY Card 1589
      Recurring Card Purchase 01/01 Google *Workspace_Am Cc@Google.Com CA Card 1589
      Payment Sent         01/20 Rmtly* J5C92 Remitly.Com WA Card 1589
      Card Purchase With Pin  01/19 Microsoft#G136262717 Redmond WA Card 1589
      ATM Withdrawal       01/05 ATM 123 Main St
      Zelle Payment To Shahzad Khan ...
    The date token (MM/DD) separates the transaction-type prefix from the merchant name.
    """
    desc = desc.strip()

    # Zelle payments — no date separator
    m = re.match(r'Zelle Payment (?:To|From)\s+(.+?)(?:\s+\d{10,}.*)?$', desc, re.I)
    if m:
        return m.group(1).strip()

    # Everything with a MM/DD date token after the prefix:
    # "Card Purchase 01/02 Henry Schein* Melville NY Card 1589"
    m = re.search(r'\d{2}/\d{2}\s+(.+?)(?:\s+Card\s+\d{4}\s*$|\s+Card\s+\d{4}\b)', desc, re.I)
    if m:
        merchant = m.group(1).strip()
        merchant = re.sub(r'\s{2,}.*$', '', merchant)
        merchant = re.sub(r'\s+Cc@\S+.*$', '', merchant, flags=re.I)
        merchant = re.sub(r'\s+[A-Z][a-z]+ [A-Z]{2}\s*$', '', merchant)
        merchant = re.sub(r'\s+[A-Z]{2}\s*$', '', merchant)
        merchant = merchant.replace('*', ' ').replace('#', ' ').strip()
        merchant = re.sub(r'\s+', ' ', merchant).strip()
        return merchant[:60]

    # Fallback: strip "Card Purchase" / "Recurring Card Purchase" / "Payment Sent" prefix
    cleaned = re.sub(
        r'^(?:Recurring\s+)?(?:Card\s+Purchase(?:\s+With\s+Pin)?|Payment\s+Sent|ATM\s+Withdrawal)\s+',
        '', desc, flags=re.I
    ).strip()
    return cleaned[:60] if cleaned else desc[:60]

def _make_memo(desc, payee, full_block=''):
    search_in = (full_block + ' ' + desc).strip()
    m = re.search(r'Trace#:(\S+)', search_in)
    if m: return f"{payee} | Tr:{m.group(1)[:16]}"
    m = re.search(r'Transaction#:\s*(\d+)', search_in)
    if m: return f"{payee} | Txn:{m.group(1)}"
    m = re.search(r'Trn:\s*(\S+)', search_in)
    if m: return f"{payee} | Trn:{m.group(1)[:16]}"
    m = re.search(r'Ind ID:(\S+)', search_in)
    if m: return f"{payee} | ID:{m.group(1)[:16]}"
    m = re.search(r'^(\d{2}/\d{2})', full_block.strip())
    date_tag = m.group(1).replace('/','') if m else ''
    return f"{payee} | {date_tag}" if date_tag else payee

def parse_pdf(pdf_path):
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber not installed. Run: pip install pdfplumber")

    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+,\s+(\d{4})', full_text)
    year = m.group(2) if m else str(datetime.now().year)
    statement_month = MONTH_NUM.get(m.group(1), datetime.now().strftime("%m")) if m else datetime.now().strftime("%m")

    acct_match = re.search(r'Account Number:\s*\n?\s*(\d{10,})', full_text, re.I)
    if not acct_match:
        acct_match = re.search(r'(\d{10,})', full_text)
    acct_id = acct_match.group(1)[-4:] if acct_match else "0000"

    def find_table_heading(pattern, offset=0):
        for m in re.finditer(pattern, full_text[offset:], re.I):
            abs_start = offset + m.start()
            abs_end = offset + m.end()
            if re.search(r'\bDATE\b', full_text[abs_end:abs_end + 160], re.I):
                return abs_start, abs_end
        return None

    def section_between(start_pat, end_pats):
        start = find_table_heading(start_pat)
        if not start:
            return ""
        end_positions = []
        for pat in end_pats:
            m_end = find_table_heading(pat, start[1])
            if m_end:
                end_positions.append(m_end[0])
        # Also stop at "Total ..." summary lines
        for total_pat in [r'\bTotal\s+Deposits\b', r'\bTotal\s+ATM\b', r'\bTotal\s+Electronic\b',
                          r'\bTotal\s+Checks\b', r'\bTotal\s+Other\s+Withdrawals\b']:
            m_tot = re.search(total_pat, full_text[start[1]:], re.I)
            if m_tot:
                end_positions.append(start[1] + m_tot.start())
        end = min(end_positions) if end_positions else len(full_text)
        return full_text[start[1]:end]

    # ── Section boundaries ──────────────────────────────────────────────────
    dep_text = section_between(
        r'\bDEPOSITS\s+AND\s+ADDITIONS\b',
        [
            r'\bATM\s+&\s+DEBIT\s+CARD\s+WITHDRAWALS\b',
            r'\bCHECKS\s+PAID\b',
            r'\bELECTRONIC\s+WITHDRAWALS\b',
            r'\bOTHER\s+WITHDRAWALS\b',
            r'\bDAILY\s+ENDING\s+BALANCE\b',
        ]
    )
    atm_text = section_between(
        r'\bATM\s+&\s+DEBIT\s+CARD\s+WITHDRAWALS\b',
        [
            r'\bATM\s+&\s+DEBIT\s+CARD\s+SUMMARY\b',
            r'\bCHECKS\s+PAID\b',
            r'\bELECTRONIC\s+WITHDRAWALS\b',
            r'\bOTHER\s+WITHDRAWALS\b',
            r'\bDAILY\s+ENDING\s+BALANCE\b',
        ]
    )
    chk_text = section_between(
        r'\bCHECKS\s+PAID\b',
        [
            r'\bELECTRONIC\s+WITHDRAWALS\b',
            r'\bOTHER\s+WITHDRAWALS\b',
            r'\bDAILY\s+ENDING\s+BALANCE\b',
        ]
    )
    elec_text = section_between(
        r'\bELECTRONIC\s+WITHDRAWALS\b',
        [
            r'\bOTHER\s+WITHDRAWALS\b',
            r'\bDAILY\s+ENDING\s+BALANCE\b',
        ]
    )
    other_text = section_between(
        r'\bOTHER\s+WITHDRAWALS\b',
        [r'\bDAILY\s+ENDING\s+BALANCE\b']
    )

    def parse_date(mmdd):
        return datetime.strptime(f"{mmdd}/{year}", "%m/%d/%Y")

    transactions = []

    # ── Generic row iterator for Orig CO Name: style sections ───────────────
    date_amt = re.compile(r'(\d{2}/\d{2})(?:\s+\d{2}/\d{2})?\s+(.*?)\s+\$?([\d,]+\.\d{2})(?:\s*$|\s+)')

    def iter_transaction_rows(section_text):
        section_text = re.sub(
            r'\*end\*[^\n]*\s/(\d{2}\s+)',
            rf'{statement_month}/\1',
            section_text,
            flags=re.I
        )
        lines = section_text.split('\n')
        i = 0
        while i < len(lines):
            m = date_amt.search(lines[i])
            if m:
                desc = m.group(2)
                if 'Total' not in desc and 'DATE' not in desc and 'Page of' not in lines[i]:
                    block = lines[i]
                    j = i + 1
                    while j < len(lines) and not date_amt.search(lines[j]) and lines[j].strip():
                        block += ' ' + lines[j].strip()
                        j += 1
                    yield m.group(1), desc, m.group(3), block
            i += 1

    # ── ATM/Debit Card row iterator ─────────────────────────────────────────
    atm_row_re = re.compile(
        r'^(\d{2}/\d{2})\s+'
        r'((?:Recurring\s+)?'
        r'(?:Card\s+Purchase(?:\s+With\s+Pin)?'
        r'|Payment\s+Sent'
        r'|ATM\s+Withdrawal'
        r'|Zelle\s+Payment\s+(?:To|From)'
        r')'
        r'.+?)'
        r'\s+\$?([\d,]+\.\d{2})\s*$',
        re.I
    )

    def iter_atm_rows(section_text):
        section_text = re.sub(r'\*end\*[^\n]*\n?', '', section_text, flags=re.I)
        lines = [l.rstrip() for l in section_text.split('\n')]
        i = 0
        while i < len(lines):
            line = lines[i]
            m = atm_row_re.match(line)
            if m:
                yield m.group(1), m.group(2).strip(), m.group(3)
            i += 1

    # ── OTHER WITHDRAWALS row iterator ──────────────────────────────────────
    # Rows look like:
    #   07/09   07/09 Withdrawal    $42.85
    #   07/09   Withdrawal           42.85
    other_row_re = re.compile(
        r'^(\d{2}/\d{2})\s+(?:\d{2}/\d{2}\s+)?(.+?)\s+\$?([\d,]+\.\d{2})\s*$',
        re.I
    )

    def iter_other_rows(section_text):
        section_text = re.sub(r'\*end\*[^\n]*\n?', '', section_text, flags=re.I)
        for line in section_text.split('\n'):
            line = line.rstrip()
            m = other_row_re.match(line)
            if m:
                desc = m.group(2).strip()
                if any(skip in desc for skip in ('Total', 'DATE', 'DESCRIPTION', 'AMOUNT')):
                    continue
                yield m.group(1), desc, m.group(3)

    # ── Parse DEPOSITS ───────────────────────────────────────────────────────
    for d, desc, amt, block in iter_transaction_rows(dep_text):
        raw = clean_name(desc, 'deposit')
        payee = normalise_payee(raw, 'deposit')
        memo = _make_memo(desc, payee, block)
        transactions.append({
            'date': parse_date(d), 'payee': payee, 'memo': memo,
            'number': 'ach', 'payment': '', 'deposit': amt.replace(',',''),
            'type': 'deposit'
        })

    # ── Parse ATM & DEBIT CARD WITHDRAWALS ──────────────────────────────────
    for d, desc, amt in iter_atm_rows(atm_text):
        payee = clean_name_atm(desc)
        payee = normalise_payee(payee, 'atm')
        inner_date = re.search(r'(\d{2}/\d{2})', desc)
        memo_tag = inner_date.group(1).replace('/', '') if inner_date else d.replace('/', '')
        memo = f"{payee} | {memo_tag}"
        transactions.append({
            'date': parse_date(d), 'payee': payee, 'memo': memo,
            'number': 'ach', 'payment': amt.replace(',',''), 'deposit': '',
            'type': 'atm'
        })

    # ── Parse CHECKS ────────────────────────────────────────────────────────
    def recover_merged_check_marker(match):
        first = match.group('first') or ''
        second = match.group('second') or ''
        tail = match.group('tail') or ''
        if tail.startswith('3') and (first or second) and len(tail) > 1:
            tail = tail[1:]
        return f"{first}{second}{tail} {match.group('rest')}"

    chk_text = re.sub(
        r'\*end\*ch(?P<first>\d*)ecks paid se(?P<second>\d*)ction(?P<tail>\d+)\s+(?P<rest>[\*\^ ]+\s*\d{2}/\d{2}.*)',
        recover_merged_check_marker,
        chk_text,
        flags=re.I
    )
    for m in re.finditer(r'^(\d{2,})\s+[\*\^ ]+(\d{2}/\d{2})(?:\s+\d{2}/\d{2})?\s+\$?([\d,]+\.\d{2})', chk_text, re.MULTILINE):
        transactions.append({
            'date': parse_date(m.group(2)), 'payee': '',
            'memo': f"Check {m.group(1)}", 'number': m.group(1),
            'payment': m.group(3).replace(',',''), 'deposit': '',
            'type': 'check'
        })

    # ── Parse ELECTRONIC WITHDRAWALS ────────────────────────────────────────
    for d, desc, amt, block in iter_transaction_rows(elec_text):
        raw = clean_name(desc, 'electronic')
        payee = normalise_payee(raw, 'electronic')
        memo = _make_memo(desc, payee, block)
        transactions.append({
            'date': parse_date(d), 'payee': payee, 'memo': memo,
            'number': 'ach', 'payment': amt.replace(',',''), 'deposit': '',
            'type': 'electronic'
        })

    # ── Parse OTHER WITHDRAWALS ─────────────────────────────────────────────
    for d, desc, amt in iter_other_rows(other_text):
        payee = normalise_payee(desc.strip(), 'other')
        if not payee:
            payee = desc[:50].strip()
        memo = f"{payee} | {d.replace('/', '')}"
        transactions.append({
            'date': parse_date(d), 'payee': payee, 'memo': memo,
            'number': 'ach', 'payment': amt.replace(',', ''), 'deposit': '',
            'type': 'other'
        })

    transactions.sort(key=lambda x: x['date'])
    return transactions, year, acct_id

# ─────────────────────────────────────────────
#  CORE: QBO WRITER
# ─────────────────────────────────────────────
def qbo_text(value, max_len=None):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if max_len is not None:
        text = text[:max_len].rstrip()
    return (
        text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )

def write_qbo(transactions, year, acct_id, out_path):
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    dates = [t['date'] for t in transactions]
    dt_start = min(dates).strftime("%Y%m%d")
    dt_end   = max(dates).strftime("%Y%m%d")
    acct_id = (re.sub(r'\D', '', acct_id) or "0000")[-4:]

    lines = [
        "OFXHEADER:100","DATA:OFXSGML","VERSION:102","SECURITY:NONE",
        "ENCODING:USASCII","CHARSET:1252","COMPRESSION:NONE",
        "OLDFILEUID:NONE","NEWFILEUID:NONE","",
        "<OFX>","  <SIGNONMSGSRSV1>","    <SONRS>",
        "      <STATUS><CODE>0<SEVERITY>INFO</STATUS>",
        f"      <DTSERVER>{now}","      <LANGUAGE>ENG",
        "      <FI>","        <ORG>INTUIT","        <FID>3000","      </FI>",
        "      <INTU.BID>3000","    </SONRS>","  </SIGNONMSGSRSV1>",
        "  <BANKMSGSRSV1>","    <STMTTRNRS>","      <TRNUID>1",
        "      <STATUS><CODE>0<SEVERITY>INFO</STATUS>",
        "      <STMTRS>","        <CURDEF>USD","        <BANKACCTFROM>",
        "          <BANKID>000000000",f"          <ACCTID>{acct_id}",
        "          <ACCTTYPE>CHECKING","        </BANKACCTFROM>",
        "        <BANKTRANLIST>",
        f"          <DTSTART>{dt_start}000000",
        f"          <DTEND>{dt_end}000000",
    ]

    for i, t in enumerate(transactions):
        dt_str = t['date'].strftime("%Y%m%d")
        if t['deposit']:
            ttype, amt = "CREDIT", f"{float(t['deposit']):.2f}"
            chk = ""
        else:
            ttype, amt = "DEBIT", f"-{float(t['payment']):.2f}"
            chk = f"\n            <CHECKNUM>{qbo_text(t['number'], 12)}" if t['number'] and t['number'] != 'ach' else ""

        raw_id = f"{acct_id}|{i:05d}|{dt_str}|{amt}|{t.get('payee','')}"
        fitid  = hashlib.md5(raw_id.encode()).hexdigest()[:20].upper()

        payee_raw = t['payee'] or f"Check {t['number']}"
        name = qbo_text(payee_raw, 32)
        memo = qbo_text(t.get('memo', payee_raw), 255)

        h, rem = divmod(i, 3600)
        m_val, s = divmod(rem, 60)
        time_str = f"{h:02d}{m_val:02d}{s:02d}"

        lines += [
            "          <STMTTRN>",
            f"            <TRNTYPE>{ttype}{chk}",
            f"            <DTPOSTED>{dt_str}{time_str}",
            f"            <TRNAMT>{amt}",
            f"            <FITID>{fitid}</FITID>",
            f"            <NAME>{name}",
            f"            <MEMO>{memo}",
            "          </STMTTRN>",
        ]

    lines += [
        "        </BANKTRANLIST>","        <LEDGERBAL>",
        "          <BALAMT>0.00",f"          <DTASOF>{now}",
        "        </LEDGERBAL>","      </STMTRS>","    </STMTTRNRS>",
        "  </BANKMSGSRSV1>","</OFX>"
    ]
    with open(out_path, 'w', encoding='ascii', errors='replace') as f:
        f.write("\n".join(lines))

# ─────────────────────────────────────────────
#  CORE: CSV WRITER
# ─────────────────────────────────────────────
def write_csv(transactions, out_path):
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Date','Number','Payee','Account','Payment','Deposit'])
        for t in transactions:
            date_str = t['date'].strftime("%-d-%b") if os.name != 'nt' else t['date'].strftime("%d-%b").lstrip('0')
            payee = t['payee'] or f"Check {t['number']}"
            account = get_account(payee)
            w.writerow([date_str, t['number'], payee, account, t['payment'], t['deposit']])

# ─────────────────────────────────────────────
#  CORE: BANK RULES GUIDE WRITER
# ─────────────────────────────────────────────
def write_rules_guide(out_path):
    rules = [
        {
            'name':      'Paymentech – CC Clearing',
            'condition': 'Payee name  contains  "Paymentech"',
            'direction': 'Money in  AND  Money out  (set BOTH)',
            'account':   'Cash in Hand:CC Clearing',
        },
        {
            'name':      'American Express – CC Clearing',
            'condition': 'Payee name  contains  "American Express"',
            'direction': 'Money in  AND  Money out  (set BOTH)',
            'account':   'Cash in Hand:CC Clearing',
        },
        {
            'name':      'Deposit – Cash Clearing',
            'condition': 'Payee name  is exactly  "Deposit"',
            'direction': 'Money in',
            'account':   'Cash in Hand:Cash Clearing',
        },
        {
            'name':      'Remote Online Deposit – Cash Clearing',
            'condition': 'Payee name  contains  "Remote Online Deposit"',
            'direction': 'Money in',
            'account':   'Cash in Hand:Cash Clearing',
        },
    ]

    lines = [
        "=" * 60,
        "  QUICKBOOKS BANK RULES – ONE-TIME SETUP GUIDE",
        "=" * 60, "",
        "HOW TO CREATE A RULE IN QUICKBOOKS:",
        "  Banking → Rules → New Rule", "",
        "-" * 60,
    ]

    for i, r in enumerate(rules, 1):
        lines += [
            f"",
            f"RULE {i}: {r['name']}",
            f"  Condition : {r['condition']}",
            f"  Direction : {r['direction']}",
            f"  Account   : {r['account']}",
            f"  Payee     : (keep as-is)",
        ]

    lines += [
        "", "-" * 60, "",
        "AFTER CREATING RULES:",
        "  1. Go to Banking → Bank Feeds",
        "  2. Click the gear icon → Reapply Rules",
        "  3. All existing unreviewed transactions will",
        "     get accounts assigned automatically.",
        "", "=" * 60,
    ]

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

# ─────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────
BLUE   = "#1a5276"
LBLUE  = "#2980b9"
GREEN  = "#27ae60"
ORANGE = "#e67e22"
BG     = "#f4f6f8"
WHITE  = "#ffffff"
DGRAY  = "#555555"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("QuickBooks Bank Statement Converter")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("780x680")
        self.transactions = []
        self._build_ui()

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BLUE, pady=12)
        hdr.pack(fill='x')
        tk.Label(hdr, text="QuickBooks Bank Statement Converter",
                 font=("Segoe UI", 16, "bold"), bg=BLUE, fg=WHITE).pack()
        tk.Label(hdr, text="Chase PDF  →  QBO / CSV  |  Works for all 14 companies",
                 font=("Segoe UI", 9), bg=BLUE, fg="#aed6f1").pack()

        frm = tk.LabelFrame(self, text=" Input ", font=("Segoe UI", 10, "bold"),
                            bg=BG, fg=BLUE, padx=10, pady=8)
        frm.pack(fill='x', padx=16, pady=(12,4))
        frm.columnconfigure(1, weight=1)

        tk.Label(frm, text="Source:", bg=BG, font=("Segoe UI",10)).grid(row=0,column=0,sticky='e',pady=4,padx=4)
        self.source_var = tk.StringVar(value="pdf")
        sf = tk.Frame(frm, bg=BG)
        sf.grid(row=0, column=1, sticky='w')
        tk.Radiobutton(sf, text="PDF Bank Statement", variable=self.source_var,
                       value="pdf", bg=BG, font=("Segoe UI",10),
                       command=self._on_source_change).pack(side='left', padx=8)
        tk.Radiobutton(sf, text="CSV File", variable=self.source_var,
                       value="csv", bg=BG, font=("Segoe UI",10),
                       command=self._on_source_change).pack(side='left')

        tk.Label(frm, text="File:", bg=BG, font=("Segoe UI",10)).grid(row=1,column=0,sticky='e',pady=4,padx=4)
        file_row = tk.Frame(frm, bg=BG)
        file_row.grid(row=1, column=1, sticky='ew')
        file_row.columnconfigure(0, weight=1)
        self.file_var = tk.StringVar()
        tk.Entry(file_row, textvariable=self.file_var, font=("Segoe UI",9)
                 ).grid(row=0, column=0, sticky='ew', padx=(0,6))
        tk.Button(file_row, text="Browse", command=self._browse,
                  bg=LBLUE, fg=WHITE, font=("Segoe UI",9),
                  relief='flat', padx=8).grid(row=0, column=1)

        tk.Label(frm, text="Save to:", bg=BG, font=("Segoe UI",10)).grid(row=2,column=0,sticky='e',pady=4,padx=4)
        out_row = tk.Frame(frm, bg=BG)
        out_row.grid(row=2, column=1, sticky='ew')
        out_row.columnconfigure(0, weight=1)
        self.out_var = tk.StringVar()
        tk.Entry(out_row, textvariable=self.out_var, font=("Segoe UI",9)
                 ).grid(row=0, column=0, sticky='ew', padx=(0,6))
        tk.Button(out_row, text="Browse", command=self._browse_out,
                  bg=LBLUE, fg=WHITE, font=("Segoe UI",9),
                  relief='flat', padx=8).grid(row=0, column=1)

        opts = tk.Frame(frm, bg=BG)
        opts.grid(row=3, column=0, columnspan=2, sticky='w', pady=4)
        tk.Label(opts, text="Account last 4 digits:", bg=BG, font=("Segoe UI",10)).pack(side='left', padx=(0,4))
        self.acct_var = tk.StringVar(value="")
        tk.Entry(opts, textvariable=self.acct_var, width=8, font=("Segoe UI",10)).pack(side='left')
        tk.Label(opts, text="  (override if wrong — auto-detected from PDF)", bg=BG,
                 font=("Segoe UI",8), fg=DGRAY).pack(side='left')

        tk.Label(frm, text="Output:", bg=BG, font=("Segoe UI",10)).grid(row=4,column=0,sticky='e',pady=4,padx=4)
        fmt_f = tk.Frame(frm, bg=BG)
        fmt_f.grid(row=4, column=1, sticky='w')
        self.fmt_qbo = tk.BooleanVar(value=True)
        self.fmt_csv = tk.BooleanVar(value=False)
        self.fmt_split = tk.BooleanVar(value=False)
        tk.Checkbutton(fmt_f, text="QBO (QuickBooks import)", variable=self.fmt_qbo,
                       bg=BG, font=("Segoe UI",10)).pack(side='left', padx=8)
        tk.Checkbutton(fmt_f, text="CSV (review/backup)", variable=self.fmt_csv,
                       bg=BG, font=("Segoe UI",10)).pack(side='left')
        tk.Checkbutton(fmt_f, text="Split QBO by section (deposits / withdrawals / checks)",
                       variable=self.fmt_split, bg=BG, font=("Segoe UI",10)).pack(side='left', padx=8)

        btn_f = tk.Frame(self, bg=BG)
        btn_f.pack(pady=8)
        tk.Button(btn_f, text="⚙  Extract & Convert", command=self._convert,
                  bg=GREEN, fg=WHITE, font=("Segoe UI",12,"bold"),
                  relief='flat', padx=20, pady=10).pack(side='left', padx=6)
        tk.Button(btn_f, text="✎  Edit Transactions", command=self._open_editor,
                  bg=ORANGE, fg=WHITE, font=("Segoe UI",12,"bold"),
                  relief='flat', padx=20, pady=10).pack(side='left', padx=6)
        tk.Button(btn_f, text="💾  Save QBO / CSV", command=self._save_files,
                  bg=BLUE, fg=WHITE, font=("Segoe UI",12,"bold"),
                  relief='flat', padx=20, pady=10).pack(side='left', padx=6)

        self.status_var = tk.StringVar(value="Ready. Browse a PDF or CSV file to begin.")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=DGRAY,
                 font=("Segoe UI",9), wraplength=740).pack()

        tbl_f = tk.LabelFrame(self, text=" Extracted Transactions (review before saving) ",
                              font=("Segoe UI",10,"bold"), bg=BG, fg=BLUE, padx=6, pady=6)
        tbl_f.pack(fill='both', expand=True, padx=16, pady=(4,12))

        cols = ('Date','Type','Payee/Check#','Number','Payment','Deposit')
        self.tree = ttk.Treeview(tbl_f, columns=cols, show='headings', height=14)
        widths = [70, 80, 280, 70, 90, 90]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=40)
        self.tree.tag_configure('deposit',    background='#eafaf1')
        self.tree.tag_configure('check',      background='#fef9e7')
        self.tree.tag_configure('electronic', background='#fdf2f8')
        self.tree.tag_configure('atm',        background='#fef5e4')
        self.tree.tag_configure('other',      background='#eaf4fb')

        vsb = ttk.Scrollbar(tbl_f, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        self.summary_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.summary_var, bg=BG, fg=BLUE,
                 font=("Segoe UI",9,"bold")).pack(pady=(0,6))

    def _on_source_change(self):
        pass

    def _browse(self):
        src = self.source_var.get()
        if src == 'pdf':
            path = filedialog.askopenfilename(title="Select PDF bank statement",
                filetypes=[("PDF","*.pdf"),("All","*.*")])
        else:
            path = filedialog.askopenfilename(title="Select CSV file",
                filetypes=[("CSV","*.csv"),("All","*.*")])
        if path:
            self.file_var.set(path)
            self.out_var.set(os.path.dirname(path))

    def _browse_out(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.out_var.set(folder)

    def _convert(self):
        path = self.file_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Error", "Please select a valid file first.")
            return

        self.status_var.set("⏳ Extracting transactions...")
        self.update()

        try:
            src = self.source_var.get()
            if src == 'pdf':
                self.transactions, self.year, detected_acct = parse_pdf(path)
                self.acct_var.set(detected_acct)
            else:
                self.transactions, self.year, detected_acct = self._load_csv(path)

            self._populate_table()
            n      = len(self.transactions)
            deps   = sum(1 for t in self.transactions if t['type'] == 'deposit')
            atms   = sum(1 for t in self.transactions if t['type'] == 'atm')
            chks   = sum(1 for t in self.transactions if t['type'] == 'check')
            elecs  = sum(1 for t in self.transactions if t['type'] == 'electronic')
            others = sum(1 for t in self.transactions if t['type'] == 'other')
            dep_total   = sum(float(t['deposit'])  for t in self.transactions if t['deposit'])
            atm_total   = sum(float(t['payment'])  for t in self.transactions if t['type'] == 'atm')
            chk_total   = sum(float(t['payment'])  for t in self.transactions if t['type'] == 'check')
            elec_total  = sum(float(t['payment'])  for t in self.transactions if t['type'] == 'electronic')
            other_total = sum(float(t['payment'])  for t in self.transactions if t['type'] == 'other')
            self.summary_var.set(
                f"✅ {n} transactions  |  Deposits: {deps} = ${dep_total:,.2f}  "
                f"|  ATM/Card: {atms} = ${atm_total:,.2f}  "
                f"|  Checks: {chks} = ${chk_total:,.2f}  "
                f"|  Electronic: {elecs} = ${elec_total:,.2f}  "
                f"|  Other: {others} = ${other_total:,.2f}"
            )
            self.status_var.set(f"Extracted {n} transactions. Review above, then click Save QBO / CSV.")
        except ImportError as e:
            messagebox.showerror("Missing Library", str(e) + "\n\nRun install_libraries.bat to fix.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not parse file:\n{e}")
            self.status_var.set(f"❌ Error: {e}")

    def _load_csv(self, path):
        rows = []
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [h.strip() for h in reader.fieldnames]
            for row in reader:
                row = {k.strip(): v.strip() for k, v in row.items()}
                for fmt in ('%d-%b','%d-%B','%m/%d','%m-%d'):
                    try:
                        d = datetime.strptime(row['Date'], fmt).replace(year=datetime.now().year)
                        break
                    except: d = None
                if not d: continue
                dep = row.get('Deposit','').replace(',','')
                pmt = row.get('Payment','').replace(',','')
                ttype = 'deposit' if dep else ('check' if row.get('Number','').isdigit() else 'electronic')
                payee = normalise_payee(row.get('Payee',''))
                rows.append({'date':d,'payee':payee,'number':row.get('Number','ach'),
                             'payment':pmt,'deposit':dep,'type':ttype})
        rows.sort(key=lambda x: x['date'])
        return rows, str(datetime.now().year), "0000"

    def _populate_table(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        type_labels = {
            'deposit':    'Deposit',
            'atm':        'ATM/Card',
            'check':      'Check',
            'electronic': 'Electronic',
            'other':      'Other',
        }
        for t in self.transactions:
            tag = t['type']
            label = type_labels.get(tag, tag.title())
            name = t['payee'] if t['payee'] else f"Check #{t['number']}"
            self.tree.insert('', 'end', values=(
                t['date'].strftime('%m/%d'), label, name, t['number'],
                f"${float(t['payment']):,.2f}" if t['payment'] else "",
                f"${float(t['deposit']):,.2f}" if t['deposit'] else ""
            ), tags=(tag,))

    def _open_editor(self):
        if not self.transactions:
            messagebox.showinfo("No data", "Extract transactions first.")
            return
        EditWindow(self, self.transactions, self._populate_table)

    def _save_files(self):
        if not self.transactions:
            messagebox.showinfo("No data", "Extract transactions first.")
            return
        out_dir = self.out_var.get().strip()
        if not out_dir:
            out_dir = os.path.dirname(self.file_var.get())
        base = os.path.splitext(os.path.basename(self.file_var.get()))[0]
        acct_id = self.acct_var.get().strip() or "0000"
        saved = []
        try:
            if self.fmt_qbo.get():
                if self.fmt_split.get():
                    # Deposits → deposits file
                    # ATM + Electronic + Other → withdrawals file
                    # Checks → checks file
                    sections = [
                        ('deposits',    [t for t in self.transactions if t['type'] == 'deposit']),
                        ('withdrawals', [t for t in self.transactions if t['type'] in ('atm', 'electronic', 'other')]),
                        ('checks',      [t for t in self.transactions if t['type'] == 'check']),
                    ]
                    for label, txns in sections:
                        if not txns:
                            continue
                        path = os.path.join(out_dir, f"{base}_{label}.qbo")
                        write_qbo(txns, self.year, acct_id, path)
                        saved.append(path)
                else:
                    new_base = simpledialog.askstring(
                        "Rename QBO file",
                        "Enter the filename to save the QBO as (without extension):",
                        initialvalue=base
                    )
                    if not new_base:
                        messagebox.showinfo("Save cancelled", "QBO save cancelled by user.")
                        return
                    qbo_path = os.path.join(out_dir, new_base.strip() + ".qbo")
                    write_qbo(self.transactions, self.year, acct_id, qbo_path)
                    saved.append(qbo_path)
            if self.fmt_csv.get():
                csv_path = os.path.join(out_dir, base + ".csv")
                write_csv(self.transactions, csv_path)
                saved.append(csv_path)
            if not saved:
                messagebox.showwarning("Nothing saved", "Select at least one output format.")
                return

            self.status_var.set(f"✅ Saved: {', '.join(os.path.basename(s) for s in saved)}")
            messagebox.showinfo(
                "Saved!",
                "Files saved:\n\n" + "\n".join(saved) +
                "\n\nImport QBO via:\n"
                "Banking → Bank Feeds → Import Web Connect File"
            )
        except Exception as e:
            messagebox.showerror("Save Error", str(e))


# ─────────────────────────────────────────────
#  EDIT WINDOW
# ─────────────────────────────────────────────
class EditWindow(tk.Toplevel):
    def __init__(self, parent, transactions, refresh_cb):
        super().__init__(parent)
        self.title("Edit Transactions")
        self.geometry("860x500")
        self.configure(bg=BG)
        self.transactions = transactions
        self.refresh_cb = refresh_cb
        self._build()

    def _build(self):
        tk.Label(self, text="Double-click a row to edit payee name. Changes reflect in the saved file.",
                 bg=BG, font=("Segoe UI",9), fg=DGRAY).pack(pady=6)
        cols = ('idx','Date','Type','Payee','Number','Payment','Deposit')
        self.tree = ttk.Treeview(self, columns=cols, show='headings')
        widths = [0,60,80,320,70,90,90]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=0)
        self.tree.column('idx', width=0, stretch=False)
        for i, t in enumerate(self.transactions):
            self.tree.insert('', 'end', iid=str(i), values=(
                i, t['date'].strftime('%m/%d'), t['type'],
                t['payee'] or f"Check {t['number']}", t['number'],
                t['payment'], t['deposit']))
        vsb = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True, padx=(8,0))
        vsb.pack(side='right', fill='y', padx=(0,8))
        self.tree.bind('<Double-1>', self._edit_row)
        tk.Button(self, text="Close", command=self.destroy,
                  bg=BLUE, fg=WHITE, font=("Segoe UI",10),
                  relief='flat', padx=12, pady=6).pack(pady=8)

    def _edit_row(self, event):
        item = self.tree.focus()
        if not item: return
        idx = int(self.tree.item(item)['values'][0])
        t = self.transactions[idx]
        win = tk.Toplevel(self)
        win.title("Edit Payee")
        win.configure(bg=BG)
        tk.Label(win, text="Payee Name:", bg=BG, font=("Segoe UI",10)).pack(padx=12, pady=(12,2))
        var = tk.StringVar(value=t['payee'])
        e = tk.Entry(win, textvariable=var, width=40, font=("Segoe UI",10))
        e.pack(padx=12, pady=4)
        e.select_range(0, 'end')
        e.focus()
        def save():
            t['payee'] = var.get().strip()
            self.tree.set(item, 'Payee', t['payee'])
            self.refresh_cb()
            win.destroy()
        tk.Button(win, text="Save", command=save,
                  bg=GREEN, fg=WHITE, font=("Segoe UI",10),
                  relief='flat', padx=10, pady=4).pack(pady=8)
        win.bind('<Return>', lambda e: save())


if __name__ == '__main__':
    app = App()
    app.mainloop()
