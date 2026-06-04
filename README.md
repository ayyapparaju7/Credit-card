# 💳 Ultimate Credit Card Payment Planner

A local, privacy-first web application built with Python and Streamlit to help users strategically manage and eliminate credit card debt. 

This tool goes beyond simple amortization calculators by offering daily ledger tracking, statement PDF extraction, custom budget allocations, and one-click monthly rollovers.

## ✨ Key Features
* **Smart PDF Extraction:** Upload your bank statements. The app uses PyMuPDF to read and extract your Balance and Minimum Due automatically (even supports unlocking password-protected PDFs).
* **Multiple Payoff Strategies:** Compare the **Debt Avalanche** (Highest APR), **Debt Snowball** (Lowest Balance), or a **Custom Allocation** method.
* **Real-World Math:** Automatically calculates base interest plus 18% GST (tax) on interest fees to give you accurate long-term projections.
* **Monthly Rollover:** A single click marks your payments as complete, adds the month's accrued interest + tax, and updates your ledger for the next month.
* **Local Privacy (SQLite):** All data and settings are stored locally in a hidden SQLite database (`debts.db`). Your financial data never leaves your machine.
* **Exportable Reports:** Download your monthly action plans and amortization schedules as `.csv` spreadsheets or clean `.pdf` reports.

## 🚀 How to Run Locally

**1. Clone the repository**
```bash
git clone [https://github.com/ayyapparaju7/Credit-card.git](https://github.com/ayyapparaju7/Credit-card.git)
cd credit-card-planner
