"""AI-powered bank statement parser.

Handles Excel (xlsx/xls) and PDF bank statements from any Indian bank
(SBI, HDFC, ICICI, Axis, PNB, etc.) by sending the raw content to
Claude and asking it to extract structured transaction data.
"""

import io
import json
import os
import re

_MAX_ROWS_FOR_PROMPT = 300   # cap rows sent to Claude to stay within token limits
_MAX_CHARS_FOR_PROMPT = 28000  # cap raw text length sent to Claude


def _get_client():
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "The 'anthropic' package is required for AI bank statement parsing. "
            "Run: pip install anthropic"
        ) from exc

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — AI bank statement parsing unavailable.")

    return anthropic.Anthropic(api_key=api_key)


def _call_claude(raw_content: str) -> list[dict]:
    """Send raw bank statement content to Claude and return parsed transactions."""
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    client = _get_client()

    system = (
        "You are a financial data extraction specialist. "
        "You extract transaction records from Indian bank statements in any format. "
        "Always respond with valid JSON only — no markdown fences, no explanations."
    )

    prompt = f"""Below is the content of a bank statement. It may be from SBI, HDFC, ICICI, Axis, PNB, Kotak, or any other Indian bank.

Extract every transaction row and return a JSON array. Each object must have exactly these keys:
- "date": string in YYYY-MM-DD format (use best guess if format is ambiguous; empty string if not found)
- "description": string (narration / particulars / transaction description)
- "debit": number (amount debited / withdrawn; 0 if not a debit)
- "credit": number (amount credited / deposited; 0 if not a credit)
- "balance": number or null (running balance after this transaction if available)

Rules:
- Skip header rows, summary rows, opening/closing balance rows, and blank rows.
- If there is a single "Amount" column with +/- signs: positive → credit, negative → debit.
- Convert all amounts to plain numbers (remove commas, currency symbols, parentheses).
- Return an empty array [] if no transactions are found.
- Return ONLY the JSON array, nothing else.

Statement content:
{raw_content[:_MAX_CHARS_FOR_PROMPT]}
"""

    message = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_response = message.content[0].text.strip()

    # Strip any accidental markdown code fences
    raw_response = re.sub(r"^```(?:json)?\s*", "", raw_response)
    raw_response = re.sub(r"\s*```$", "", raw_response)

    # Normalize whitespace so literal newlines/tabs inside string values don't
    # break JSON parsing. JSON permits spaces between tokens, so this is safe.
    raw_response = raw_response.replace("\r", " ").replace("\n", " ").replace("\t", " ")

    def _fix_control_chars_in_strings(s: str) -> str:
        """Replace control characters inside JSON string values with a space."""
        result = []
        in_string = False
        escape_next = False
        for ch in s:
            if escape_next:
                result.append(ch)
                escape_next = False
            elif ch == "\\" and in_string:
                result.append(ch)
                escape_next = True
            elif ch == '"':
                in_string = not in_string
                result.append(ch)
            elif in_string and ord(ch) < 0x20:
                result.append(" ")
            else:
                result.append(ch)
        return "".join(result)

    def _remove_trailing_commas(s: str) -> str:
        return re.sub(r",\s*([}\]])", r"\1", s)

    def _extract_json_array(s: str) -> str | None:
        start = s.find("[")
        end = s.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        return s[start:end + 1]

    def _salvage_truncated_json_array(s: str):
        if not s.strip().startswith("["):
            return None
        last_closing = s.rfind("}")
        if last_closing == -1:
            return None
        candidate = s[: last_closing + 1].rstrip() + "]"
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    def _try_load_json(text: str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    data = _try_load_json(raw_response)
    if data is None:
        data = _try_load_json(_fix_control_chars_in_strings(raw_response))
    if data is None:
        data = _try_load_json(_remove_trailing_commas(raw_response))
    if data is None:
        data = _try_load_json(_remove_trailing_commas(_fix_control_chars_in_strings(raw_response)))
    if data is None:
        extracted = _extract_json_array(raw_response)
        if extracted is not None:
            data = _try_load_json(extracted)
            if data is None:
                data = _try_load_json(_remove_trailing_commas(extracted))
        if data is None:
            extracted = _extract_json_array(_fix_control_chars_in_strings(raw_response))
            if extracted is not None:
                data = _try_load_json(extracted)
                if data is None:
                    data = _try_load_json(_remove_trailing_commas(extracted))
    if data is None:
        data = _salvage_truncated_json_array(raw_response)
    if data is None:
        raise ValueError(f"Claude returned non-JSON response: {raw_response[:300]}")

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array from Claude, got: {type(data).__name__}")

    return data


def _normalize_rows(raw_rows: list[dict]) -> list[dict]:
    """Convert Claude's output to the standard import_workflow row format."""
    results = []
    for idx, item in enumerate(raw_rows, start=1):
        try:
            debit = float(item.get("debit") or 0)
            credit = float(item.get("credit") or 0)
            amount = round(credit - debit, 2)

            balance_raw = item.get("balance")
            try:
                balance = float(balance_raw) if balance_raw not in (None, "", "null") else None
            except (TypeError, ValueError):
                balance = None

            results.append({
                "row_index": idx,
                "date": str(item.get("date") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "amount": amount,
                "running_balance": balance,
                "raw_row": item,
                "parser": "ai_claude",
            })
        except Exception:
            # Skip malformed rows silently; they show up in warning count
            continue
    return results


def parse_xlsx(file_bytes: bytes, password: str = "") -> list[dict]:
    """Parse an Excel bank statement (xlsx/xls) using Claude AI.

    Converts the sheet to plain text then asks Claude to extract transactions.
    If the file is password-protected, attempts decryption with the supplied
    password. Raises ValueError("EXCEL_PASSWORD_REQUIRED: ...") if the file is
    encrypted and no (or wrong) password is given.
    """
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("The 'openpyxl' package is required. Run: pip install openpyxl") from exc

    actual_bytes = file_bytes

    try:
        import msoffcrypto  # noqa: PLC0415
    except ImportError:
        msoffcrypto = None

    if msoffcrypto is not None:
        try:
            office_file = msoffcrypto.OfficeFile(io.BytesIO(file_bytes))
            if office_file.is_encrypted():
                if not password:
                    raise ValueError(
                        "EXCEL_PASSWORD_REQUIRED: This Excel file is password-protected. "
                        "Please provide the password (e.g. your date of birth or account number)."
                    )
                decrypted = io.BytesIO()
                office_file.load_key(password=password)
                office_file.decrypt(decrypted)
                actual_bytes = decrypted.getvalue()
        except ValueError:
            raise
        except Exception as exc:
            err_lower = str(exc).lower()
            if any(kw in err_lower for kw in ("password", "encrypt", "incorrect", "decrypt", "invalid password")):
                raise ValueError(
                    "EXCEL_PASSWORD_REQUIRED: Incorrect password for this Excel file. Please try again."
                ) from exc
            # If msoffcrypto cannot parse this file as an Office workbook, continue
            # and let openpyxl handle the failure path instead.
            pass

    try:
        wb = openpyxl.load_workbook(io.BytesIO(actual_bytes), read_only=True, data_only=True)
    except Exception as exc:
        err_lower = str(exc).lower()
        if any(kw in err_lower for kw in ("zip", "encrypt", "password", "not a zip")):
            raise ValueError(
                "EXCEL_PASSWORD_REQUIRED: This Excel file appears to be password-protected. "
                "Please provide the password."
            ) from exc
        raise

    ws = wb.active  # use first/active sheet

    lines = []
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row_idx > _MAX_ROWS_FOR_PROMPT:
            break
        parts = []
        for cell in row:
            if cell is None:
                parts.append("")
            else:
                # Strip ALL control characters (U+0000–U+001F, U+007F) from cell
                # values. SBI/HDFC email exports often embed vertical tabs, form
                # feeds, etc. inside narration strings, which break JSON later.
                cell_str = re.sub(r'[\x00-\x1f\x7f]', ' ', str(cell))
                parts.append(cell_str.strip())
        line = "\t".join(parts).strip()
        if line:
            lines.append(line)

    raw_content = "\n".join(lines)
    if not raw_content.strip():
        return []

    raw_rows = _call_claude(raw_content)
    return _normalize_rows(raw_rows)


def parse_pdf(file_bytes: bytes, password: str = "") -> list[dict]:
    """Parse a PDF bank statement using Claude AI.

    Extracts text with pdfplumber then asks Claude to extract transactions.
    Raises ValueError("PDF_PASSWORD_REQUIRED: ...") if the PDF is encrypted
    and no (or wrong) password is supplied.
    """
    try:
        import pdfplumber  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("The 'pdfplumber' package is required. Run: pip install pdfplumber") from exc

    text_parts = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes), password=password or "") as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as exc:
        err_lower = str(exc).lower()
        if any(kw in err_lower for kw in ("password", "encrypt", "incorrect", "decrypt", "protected")):
            raise ValueError(
                "PDF_PASSWORD_REQUIRED: This PDF is password-protected. "
                "Please provide the password (e.g. your date of birth or account number)."
            ) from exc
        raise

    raw_content = "\n".join(text_parts)
    if not raw_content.strip():
        return []

    raw_rows = _call_claude(raw_content)
    return _normalize_rows(raw_rows)


def check_file_needs_password(file_bytes: bytes, extension: str, password: str = "") -> None:
    """Pre-flight check: raise ValueError if file is encrypted and no/wrong password given.

    Call this synchronously BEFORE creating any batch documents so that the API
    handler can return a 400 immediately rather than creating a "failed" batch.
    """
    if extension in ("xlsx", "xls"):
        try:
            import msoffcrypto  # noqa: PLC0415
        except ImportError:
            return  # can't check without msoffcrypto; let the async job fail gracefully
        try:
            office_file = msoffcrypto.OfficeFile(io.BytesIO(file_bytes))
            if not office_file.is_encrypted():
                return
            if not password:
                raise ValueError(
                    "EXCEL_PASSWORD_REQUIRED: This Excel file is password-protected. "
                    "Please provide the password (e.g. your date of birth or account number)."
                )
            # Verify the supplied password is actually correct
            try:
                office_file.load_key(password=password)
                office_file.decrypt(io.BytesIO())
            except Exception as exc:
                raise ValueError(
                    "EXCEL_PASSWORD_REQUIRED: Incorrect password for this Excel file. Please try again."
                ) from exc
        except ValueError:
            raise
        except Exception:
            pass  # unexpected msoffcrypto error — let the async job handle it

    elif extension == "pdf":
        try:
            import pdfplumber  # noqa: PLC0415
        except ImportError:
            return
        try:
            with pdfplumber.open(io.BytesIO(file_bytes), password=password or "") as pdf:
                # Just open the PDF to verify password works; don't read all pages
                _ = len(pdf.pages)
        except Exception as exc:
            err_lower = str(exc).lower()
            if any(kw in err_lower for kw in ("password", "encrypt", "incorrect", "decrypt", "protected")):
                if not password:
                    raise ValueError(
                        "PDF_PASSWORD_REQUIRED: This PDF is password-protected. "
                        "Please provide the password (e.g. your date of birth or account number)."
                    ) from exc
                raise ValueError(
                    "PDF_PASSWORD_REQUIRED: Incorrect PDF password. Please try again."
                ) from exc
