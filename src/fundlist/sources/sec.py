from __future__ import annotations

import time
import urllib.error
from typing import Dict, List, Sequence

from ..http import http_get_json
from ..models import Item, to_item


def ticker_to_cik(user_agent: str) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    data = http_get_json("https://www.sec.gov/files/company_tickers.json", headers=headers)

    mapping: Dict[str, str] = {}
    for row in data.values():
        ticker = str(row.get("ticker", "")).upper()
        cik = str(row.get("cik_str", "")).strip()
        if ticker and cik:
            mapping[ticker] = cik.zfill(10)
    return mapping


def collect_sec_filings(
    tickers: Sequence[str],
    per_ticker: int,
    user_agent: str,
) -> List[Item]:
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    ticker_map = ticker_to_cik(user_agent=user_agent)
    out: List[Item] = []

    for ticker_raw in tickers:
        ticker = ticker_raw.strip().upper()
        if not ticker:
            continue

        cik = ticker_map.get(ticker)
        if not cik:
            continue

        try:
            data = http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers)
        except urllib.error.HTTPError:
            continue

        recent = ((data.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accession_numbers = recent.get("accessionNumber") or []
        primary_documents = recent.get("primaryDocument") or []

        count = min(per_ticker, len(forms), len(filing_dates), len(accession_numbers), len(primary_documents))
        for idx in range(count):
            form = forms[idx]
            filing_date = filing_dates[idx]
            accession = accession_numbers[idx]
            primary_doc = primary_documents[idx]
            acc_no_dash = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/{primary_doc}"
                if primary_doc
                else f"https://www.sec.gov/edgar/browse/?CIK={ticker}"
            )

            out.append(
                to_item(
                    source="sec_edgar",
                    category="filing",
                    symbol=ticker,
                    title=f"{ticker} filed {form} on {filing_date}",
                    url=filing_url,
                    published_at=f"{filing_date}T00:00:00+00:00",
                    payload={
                        "ticker": ticker,
                        "cik": cik,
                        "form": form,
                        "filing_date": filing_date,
                        "accession_number": accession,
                        "primary_document": primary_doc,
                    },
                )
            )

        # Be conservative with SEC rate expectations.
        time.sleep(0.2)

    return out

