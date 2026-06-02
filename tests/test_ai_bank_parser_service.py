import io
from unittest.mock import MagicMock, patch

import msoffcrypto
import pytest

from smart_invoice_pro.services.ai_bank_parser_service import check_file_needs_password, _call_claude, parse_xlsx


def test_check_file_needs_password_encrypted_xlsx_requires_password():
    mock_office = MagicMock()
    mock_office.is_encrypted.return_value = True

    with patch.object(msoffcrypto, "OfficeFile", return_value=mock_office):
        with pytest.raises(ValueError, match="EXCEL_PASSWORD_REQUIRED"):
            check_file_needs_password(b"fake-xlsx-bytes", "xlsx", password="")


def test_check_file_needs_password_encrypted_xlsx_wrong_password():
    mock_office = MagicMock()
    mock_office.is_encrypted.return_value = True
    mock_office.load_key.return_value = None
    mock_office.decrypt.side_effect = RuntimeError("incorrect password")

    with patch.object(msoffcrypto, "OfficeFile", return_value=mock_office):
        with pytest.raises(ValueError, match="Incorrect password"):
            check_file_needs_password(b"fake-xlsx-bytes", "xlsx", password="wrong")


def test_call_claude_recovers_from_trailing_comma_and_extra_text():
    raw_text = (
        "The parser output is below:\n"
        "[\n  {\"date\": \"2026-02-01\", \"description\": \"Test\", "
        "\"debit\": 0, \"credit\": 100, \"balance\": 100,},\n]\n"
        "End of response."
    )

    fake_message = MagicMock()
    fake_message.content = [MagicMock(text=raw_text)]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_message

    with patch("smart_invoice_pro.services.ai_bank_parser_service._get_client", return_value=fake_client):
        rows = _call_claude("dummy content")

    assert isinstance(rows, list)
    assert rows == [{"date": "2026-02-01", "description": "Test", "debit": 0, "credit": 100, "balance": 100}]


def test_parse_xlsx_decrypts_with_msoffcrypto_and_returns_rows():
    mock_office = MagicMock()
    mock_office.is_encrypted.return_value = True
    mock_office.load_key.return_value = None

    def decrypt(buf):
        buf.write(b"PK\x03\x04fakexlsx")

    mock_office.decrypt.side_effect = decrypt

    with patch.object(msoffcrypto, "OfficeFile", return_value=mock_office), \
         patch("openpyxl.load_workbook") as mock_load_workbook, \
         patch("smart_invoice_pro.services.ai_bank_parser_service._call_claude") as mock_call_claude:
        fake_ws = MagicMock()
        fake_ws.iter_rows.return_value = [
            ("2026-02-01", "Test transaction", 0, 100.0, 100.0),
        ]
        fake_wb = MagicMock(active=fake_ws)
        mock_load_workbook.return_value = fake_wb
        mock_call_claude.return_value = [
            {
                "date": "2026-02-01",
                "description": "Test transaction",
                "debit": 0,
                "credit": 100.0,
                "balance": 100.0,
            }
        ]

        rows = parse_xlsx(b"fake-xlsx-bytes", password="secret")

    assert rows[0]["date"] == "2026-02-01"
    assert rows[0]["description"] == "Test transaction"
    assert rows[0]["amount"] == 100.0
