# QuickBooks Bank Statement Converter

A desktop GUI application that converts Chase bank statement PDFs into QuickBooks-compatible `.qbo` files for direct import into QuickBooks Desktop.

## Features

- **PDF to QBO Conversion** — Extracts transactions from Chase PDF bank statements and generates `.qbo` (Web Connect) files
- **CSV Export** — Optionally export transactions to CSV for review or backup
- **Transaction Editor** — Edit payee names before saving
- **Split QBO Output** — Split into separate files by section (deposits / withdrawals / checks)
- **Auto-detection** — Automatically detects account number and statement year from the PDF
- **Color-coded Review** — Transactions displayed in a color-coded table for easy review

## Screenshots

*Transactions are extracted, reviewed in the table, then saved as QBO for QuickBooks import.*

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Steps

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/qb-converter.git
   cd qb-converter
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

   Or on Windows, simply double-click `install_libraries.bat`.

## Usage

### Running the Application

**Option 1 — Double-click `run.bat` (Windows)**

**Option 2 — Command line:**
```bash
python src/app.py
```

**Option 3 — Install as a package:**
```bash
pip install -e .
qb-converter
```

### Converting a Statement

1. Launch the application
2. Select **PDF Bank Statement** as the source
3. Click **Browse** and select your Chase PDF statement
4. Choose an output folder
5. Click **Extract & Convert**
6. Review the transactions in the table
7. (Optional) Double-click any row to edit the payee name
8. Click **Save QBO / CSV**
9. In QuickBooks Desktop: **Banking → Bank Feeds → Import Web Connect File** and select your `.qbo` file

## Project Structure

```
dist/
├── src/
│   ├── __init__.py
│   └── app.py              # Main application (GUI + parser + QBO writer)
├── requirements.txt        # Python dependencies
├── setup.py                # Package setup script
├── pyproject.toml          # Modern Python project configuration
├── run.bat                 # Windows launcher
├── install_libraries.bat   # Windows dependency installer
├── LICENSE                 # MIT License
├── .gitignore              # Git ignore rules
└── README.md               # This file
```

## Supported Statement Sections

The parser handles the following Chase statement sections:

| Section | Description |
|---------|-------------|
| Deposits and Additions | All deposits including ACH, checks, and card settlements |
| ATM & Debit Card Withdrawals | Card purchases, ATM withdrawals, Zelle payments |
| Checks Paid | Check payments with check numbers |
| Electronic Withdrawals | ACH debits, online payments |
| Other Withdrawals | Miscellaneous withdrawals |

## QuickBooks Bank Rules Setup

After importing your QBO file, you may want to set up bank rules in QuickBooks for automatic categorization. The app can generate a rules guide:

```python
from src.app import write_rules_guide
write_rules_guide("rules_guide.txt")
```

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Disclaimer

This tool is not affiliated with or endorsed by Intuit or Chase. It is an independent utility designed to facilitate data conversion between formats.
