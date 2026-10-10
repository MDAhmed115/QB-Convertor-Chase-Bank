from setuptools import setup, find_packages

setup(
    name="qb-converter",
    version="1.0.0",
    description="Convert Chase bank statement PDFs to QuickBooks-compatible QBO files",
    author="QB Converter Contributors",
    license="MIT",
    packages=find_packages(),
    install_requires=[
        "pdfplumber>=0.9.0",
    ],
    entry_points={
        "console_scripts": [
            "qb-converter=src.app:main",
        ],
    },
    python_requires=">=3.8",
)
