import os
import tempfile
import json
import time
import unittest
import email
import imaplib
from pathlib import Path
from email.message import EmailMessage
from email.policy import default

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mailassistant_qt.logic import (
    MailClient, decode_imap_folder_name, decode_mime_parameter, decode_value,
)
from mailassistant_qt.documents import DocumentIndex
from mailassistant_qt.documents import (
    extract_pdf_text, interpret_natural_query, local_ocr_available, ocr_pdf,
    recognize_document_text,
)
from mailassistant_qt.documents_ui import DocumentPreviewPage, DocumentsPage
from mailassistant_qt.devices import create_test_pdf
from unittest.mock import MagicMock, patch


def make_searchable_pdf(path, text="Podpisany formularz Kowalskiej"):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 18 Tf 72 760 Td ({text}) Tj ET".encode("ascii")
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    with open(path, "wb") as output:
        output.write(content)
    return path
from mailassistant_qt.main_window import MailPage, MessagePage
from mailassistant_qt.core import user_error


class FakeFolderIMAP:
    def __init__(self):
        self.current = "INBOX"
        self.search_calls = []
        self.ids = {
            "INBOX": [str(i).encode() for i in range(1, 121)],
            "Sent": [b"201", b"202"],
            "Drafts": [b"301"],
            "Archive": [b"401"],
        }

    def list(self):
        return "OK", [
            b'(\\HasNoChildren \\Inbox) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
            b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
            b'(\\HasNoChildren \\Archive) "/" "Archive"',
            b'(\\HasNoChildren \\Trash) "/" "Trash"',
            b'(\\HasNoChildren) "/" "Rodzina"',
        ]

    def select(self, folder, readonly=True):
        self.current = folder.decode() if isinstance(folder, bytes) else str(folder)
        return "OK", [str(len(self.ids.get(self.current, []))).encode()]

    def response(self, name):
        return "OK", [b"777" if self.current == "INBOX" else b"888"]

    def uid(self, operation, *args):
        if operation == "search":
            self.search_calls.append(args)
            return "OK", [b" ".join(self.ids.get(self.current, []))]
        if operation == "fetch":
            uid = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            request = str(args[1])
            message = EmailMessage()
            message["From"] = f"Nadawca {uid} <n{uid}@example.com>"
            message["To"] = "Odbiorca <odbiorca@example.com>"
            message["Subject"] = f"Bardzo długi temat wiadomości numer {uid} dotyczący ważnych dokumentów rodzinnych"
            message["Date"] = "Tue, 27 Oct 2026 10:30:00 +0100"
            message["Message-ID"] = f"<{uid}@example.com>"
            message.set_content(f"Krótki fragment treści wiadomości {uid}.")
            if request == "(BODY.PEEK[])":
                return "OK", [(b"message", message.as_bytes())]
            header = (
                f"From: {message['From']}\r\nTo: {message['To']}\r\nSubject: {message['Subject']}\r\n"
                f"Date: {message['Date']}\r\nMessage-ID: {message['Message-ID']}\r\n\r\n"
            ).encode()
            flags = b"\\Seen" if int(uid) % 2 == 0 else b""
            protocol = (
                b"1 (FLAGS (" + flags + b") BODYSTRUCTURE (\"APPLICATION\" \"PDF\" "
                b"(\"NAME\" \"\xc5\x9bwiadczenia.pdf\") NIL "
                b"(\"FILENAME\" \"\xc5\x9bwiadczenia.pdf\")))"
            )
            return "OK", [(protocol, header), (b"BODY[TEXT]", f"Fragment {uid}".encode()), b")"]
        raise AssertionError(operation)


class BatchFolderIMAP(imaplib.IMAP4):
    """Minimalny prawdziwy typ IMAP, który rejestruje zbiorcze FETCH."""

    def __init__(self):
        self.fetch_calls = []
        self.fetch_requests = []

    def select(self, folder, readonly=True):
        return "OK", [b"3"]

    def response(self, name):
        return "OK", [b"999"]

    def uid(self, operation, *args):
        if operation == "search":
            return "OK", [b"1 2 3"]
        if operation != "fetch":
            raise AssertionError(operation)
        sequence = args[0]
        self.fetch_calls.append(sequence)
        self.fetch_requests.append(str(args[1]))
        response = []
        for uid in bytes(sequence).split(b","):
            header = (
                b"From: Anna <anna@example.com>\r\n"
                b"To: Damian <damian@example.com>\r\n"
                b"Subject: Test zbiorczy\r\n"
                b"Date: Tue, 27 Oct 2026 10:30:00 +0100\r\n"
                b"Message-ID: <batch-" + uid + b"@example.com>\r\n\r\n"
            )
            response.extend([
                (b"1 (UID " + uid + b" FLAGS () BODYSTRUCTURE (\"TEXT\" \"PLAIN\")", header),
                (b" BODY[TEXT]<0> {15}", b"Fragment testu"),
                b")",
            ])
        return "OK", response


class SearchFallbackIMAP:
    def __init__(self):
        self.calls = []

    def uid(self, operation, *args):
        self.calls.append((operation, args))
        if operation != "search":
            raise AssertionError(operation)
        if args and args[0] == "UTF-8":
            return "NO", [b""]
        return "OK", [b"17 23"]


class IndexedPdfIMAP:
    def select(self, folder, readonly=True):
        self.folder = folder
        self.readonly = readonly
        return "OK", [b"1"]

    def response(self, _name):
        return "OK", [b"1792763100"]

    def uid(self, operation, server_uid, request):
        if operation != "fetch" or str(server_uid) != "4" or request != "(BODY.PEEK[])":
            raise AssertionError((operation, server_uid, request))
        message = EmailMessage()
        message["From"] = "Anna <anna@example.com>"
        message["To"] = "konto@example.com"
        message["Subject"] = "test"
        message.set_content("Wiadomość z CV")
        message.add_attachment(
            b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF",
            maintype="application", subtype="pdf", filename="Main_CV.pdf",
        )
        return "OK", [(b"BODY[]", message.as_bytes())]


class MailCenterPhaseOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def client(self):
        client = MailClient()
        client.connection = FakeFolderIMAP()
        return client

    def test_01_detects_real_imap_folder_roles(self):
        folders = self.client().list_folders()
        roles = {item["role"]: item["id"] for item in folders}
        self.assertEqual(roles["inbox"], "INBOX")
        self.assertEqual(roles["sent"], "Sent")
        self.assertEqual(roles["drafts"], "Drafts")
        self.assertEqual(roles["archive"], "Archive")
        self.assertEqual(roles["trash"], "Trash")
        self.assertTrue(any(item["role"] == "other" and item["id"] == "Rodzina" for item in folders))
        self.assertEqual(decode_imap_folder_name("Wys&AUI-ane prywatne"), "Wysłane prywatne")

    def test_02_inbox_and_sent_use_distinct_stable_references(self):
        client = self.client()
        inbox = client.load_folder("INBOX", 0, 1)
        sent = client.load_folder("Sent", 0, 1)
        self.assertNotEqual(inbox["rows"][0]["uid"], sent["rows"][0]["uid"])
        self.assertEqual(inbox["rows"][0]["folder"], "INBOX")
        self.assertEqual(sent["rows"][0]["folder"], "Sent")

    def test_03_pagination_loads_older_messages_without_full_mail(self):
        client = self.client()
        first = client.load_folder("INBOX", 0, 50)
        older = client.load_folder("INBOX", 50, 50)
        self.assertEqual(first["total"], 120)
        self.assertEqual(len(first["rows"]), 50)
        self.assertEqual(len(older["rows"]), 50)
        self.assertNotEqual(first["rows"][-1]["server_uid"], older["rows"][0]["server_uid"])
        self.assertTrue(all(row["snippet"] for row in first["rows"]))
        self.assertEqual(first["rows"][0]["attachment_names"], ["świadczenia.pdf"])

    def test_04_search_uses_server_for_old_messages_and_dates(self):
        client = self.client()
        client.load_folder("INBOX", 0, 20, "Kowalska")
        self.assertIn("UTF-8", client.connection.search_calls[-1])
        client.load_folder("INBOX", 0, 20, "27.10.2026")
        self.assertIn("ON", client.connection.search_calls[-1])

    def test_04b_polish_search_never_silently_falls_back_to_all_messages(self):
        connection = SearchFallbackIMAP()
        client = MailClient()
        client.connection = connection
        ids = client._search_ids("Łukasz formularz")
        self.assertEqual(ids, [b"17", b"23"])
        search_arguments = [args for operation, args in connection.calls if operation == "search"]
        self.assertEqual(len(search_arguments), 2)
        self.assertTrue(all("ALL" not in args for args in search_arguments))
        self.assertIn(b'"\xc5\x81ukasz"', search_arguments[0])

    def test_05_long_subject_is_not_elided_and_has_snippet(self):
        page = MailPage()
        page.set_folders([{"id": "INBOX", "name": "Odebrane", "role": "inbox"}])
        subject = "To jest wyjątkowo długi temat, którego nie wolno ucinać w sposób utrudniający zrozumienie wiadomości"
        page.set_mailbox({
            "folder": "INBOX", "total": 1, "offset": 0, "limit": 50,
            "rows": [{"uid": "1", "subject": subject, "sender": "Anna <anna@example.com>",
                      "date": "27.10.2026", "snippet": "Czytelny fragment treści", "unread": True,
                      "has_attachments": True, "folder": "INBOX"}],
        })
        item = page.message_list.item(0)
        accessible = str(item.data(0, Qt.ItemDataRole.AccessibleTextRole) or "")
        self.assertIn(subject, accessible)
        self.assertIn("Czytelny fragment", accessible)
        self.assertEqual(page.message_list.textElideMode(), Qt.TextElideMode.ElideRight)
        page.deleteLater()

    def test_06_mail_list_has_no_embedded_message_preview(self):
        page = MailPage()
        page.resize(700, 700)
        page.show()
        self.app.processEvents()
        self.assertEqual(page.splitter.orientation(), Qt.Orientation.Horizontal)
        self.assertTrue(page.compact_folder_row.isVisible())
        self.assertFalse(page.folder_card.isVisible())
        self.assertEqual(page.splitter.count(), 2)
        self.assertTrue(page.mail_list_card.isVisible())
        page.close()

        message = MessagePage()
        message.show_message(
            "mail-1",
            {"subject": "Czytelny temat", "sender": "Anna", "recipient": "Jan", "date": "27.10.2026"},
            "Pełna treść wiadomości wyświetlana na oddzielnej stronie.",
            ["formularz.pdf"],
        )
        self.assertIn("Pełna treść", message.preview.toPlainText())
        self.assertEqual(message.attachment_names, ["formularz.pdf"])
        message.deleteLater()

    def test_06a_short_window_scrolls_message_without_overlapping_attachments(self):
        message = MessagePage()
        message.show_message(
            "mail-small-window",
            {"subject": "Dokumenty do podpisu", "sender": "Anna", "recipient": "Jan", "date": "27.10.2026"},
            "Długa treść wiadomości. " * 250,
            ["Formularz_TEST04A_nowy.pdf"],
        )
        message.resize(620, 420)
        message.show()
        self.app.processEvents()
        self.assertGreater(
            message.attachment_card.geometry().top(),
            message.message_card.geometry().bottom(),
        )
        self.assertGreater(message.scroll.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(message.attachments.minimumHeight(), 90)
        message.close()
        message.deleteLater()

    def test_06b_all_folder_search_does_not_replace_the_real_current_folder(self):
        page = MailPage()
        page.current_folder = "INBOX"
        page.set_mailbox({
            "folder": "__ALL__", "rows": [], "total": 0, "offset": 0,
            "limit": 50, "query": "nieistniejący temat",
        })
        self.assertEqual(page.current_folder, "INBOX")
        self.assertIn("Nie znaleziono", page.status.text())
        requests = []
        page.mailbox_requested.connect(lambda *args: requests.append(args))
        page._clear_search()
        self.assertEqual(requests[-1], ("INBOX", 0, 50, ""))
        page.deleteLater()

    def test_06c_indexed_pdf_is_downloaded_without_opening_mail_first(self):
        client = MailClient()
        client.connection = IndexedPdfIMAP()
        attachment = client.get_indexed_attachment("INBOX", "1792763100", "4", 0)
        self.assertEqual(attachment["name"], "Main_CV.pdf")
        self.assertTrue(client.connection.readonly)
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as directory:
            target = Path(directory) / "Main_CV.pdf"
            client.save_attachment(attachment, target)
            self.assertTrue(target.read_bytes().startswith(b"%PDF-"))

    def test_06d_multipart_preview_never_shows_content_type_or_boundary(self):
        message = EmailMessage()
        message["From"] = "Damian <damian@example.com>"
        message["Subject"] = "Dokumenty do podpisu"
        message.set_content("Dzień dobry, przesyłam dokumenty do podpisu.")
        message.add_alternative("<p>Dzień dobry, przesyłam dokumenty do podpisu.</p>", subtype="html")
        raw = message.as_bytes()
        header, body = raw.split(b"\n\n", 1)
        preview = email.message_from_bytes(header + b"\n\n" + body[:1024], policy=default)
        snippet = MailClient._plain_snippet(preview, body[:1024])
        self.assertIn("Dzień dobry", snippet)
        self.assertNotIn("Content-Type", snippet)
        self.assertNotIn("boundary", snippet.lower())

    def test_06e_polish_headers_support_utf8_cp1250_and_mojibake_repair(self):
        expected = "zaświadczenie.pdf"
        self.assertEqual(decode_value(expected.encode("utf-8")), expected)
        self.assertEqual(decode_value(expected.encode("cp1250")), expected)
        self.assertEqual(decode_value("zaÅ›wiadczenie.pdf"), expected)

    def test_06f_rfc2231_polish_pdf_name_is_decoded(self):
        self.assertEqual(
            decode_mime_parameter(b"utf-8''za%C5%9Bwiadczenie.pdf"),
            "zaświadczenie.pdf",
        )

    def test_06g_wrong_declared_charset_does_not_destroy_polish_body(self):
        expected = "Zażółć gęślą jaźń. Proszę podpisać formularz."
        raw = (
            b'MIME-Version: 1.0\r\nContent-Type: text/plain; charset="utf-8"\r\n'
            b'Content-Transfer-Encoding: 8bit\r\n\r\n' + expected.encode("cp1250")
        )
        client = MailClient()
        client.messages["polish"] = email.message_from_bytes(raw, policy=default)
        body, _attachments = client.get_details("polish")
        self.assertEqual(body, expected)
        self.assertNotIn("�", body)

    def test_06h_page_headers_are_fetched_once_and_reused_in_session(self):
        client = MailClient()
        client.connection = BatchFolderIMAP()
        first = client.load_folder("INBOX", 0, 3, "")
        self.assertEqual(len(first["rows"]), 3)
        self.assertEqual(client.connection.fetch_calls, [b"3,2,1"])
        second = client.load_folder("INBOX", 0, 3, "")
        self.assertEqual(len(second["rows"]), 3)
        self.assertEqual(client.connection.fetch_calls, [b"3,2,1"])
        client.clear_folder_cache("INBOX")
        client.load_folder("INBOX", 0, 3, "")
        self.assertEqual(client.connection.fetch_calls, [b"3,2,1", b"3,2,1"])

    def test_06i_document_scan_does_not_download_message_body(self):
        client = MailClient()
        client.connection = BatchFolderIMAP()
        client.load_document_folder("INBOX", 0, 3)
        self.assertNotIn("BODY.PEEK[TEXT]", client.connection.fetch_requests[0])
        # Lekki wpis dokumentowy nie zatruwa pamięci podręcznej zwykłej listy,
        # która nadal może pobrać czytelny fragment wiadomości.
        client.load_folder("INBOX", 0, 3, "")
        self.assertEqual(client.connection.fetch_calls, [b"3,2,1", b"3,2,1"])
        self.assertIn("BODY.PEEK[TEXT]", client.connection.fetch_requests[1])


class DocumentLibraryPhaseTwoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.index = DocumentIndex(os.path.join(self.temp.name, "documents.sqlite3"))
        self.account = "konto@wp.pl"
        self.row = {
            "uid": "mail:abc", "server_uid": "42", "folder": "INBOX", "uid_validity": "777",
            "message_identity": "INBOX|777|42", "progress_id": "INBOX|777|42|<x@example.com>",
            "sender": "Anna Kowalska <anna@example.com>", "recipient": "konto@wp.pl",
            "subject": "Formularz do podpisania", "date": "27.10.2026, 10:30",
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_pdf(self, name):
        path = os.path.join(self.temp.name, name)
        with open(path, "wb") as stream:
            stream.write(b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF")
        return path

    def test_07_received_attachments_are_indexed_without_copying(self):
        ids = self.index.index_received_attachments(self.account, self.row, ["formularz.pdf", "instrukcja.pdf"])
        values = self.index.search(self.account, category="received")
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(values), 2)
        self.assertTrue(all(not item["local_path"] and item["server_available"] for item in values))

    def test_08_repeated_indexing_does_not_duplicate_document(self):
        self.index.index_received_attachments(self.account, self.row, ["formularz.pdf"])
        self.index.index_received_attachments(self.account, self.row, ["formularz.pdf"])
        self.assertEqual(len(self.index.search(self.account)), 1)

    def test_08b_received_and_sent_copy_is_one_logical_document(self):
        self.index.index_received_attachments(self.account, self.row, ["skan_test.pdf"])
        sent = dict(self.row)
        sent.update({
            "uid": "mail:sent", "server_uid": "84", "folder": "Sent", "uid_validity": "888",
            "message_identity": "Sent|888|84", "progress_id": "Sent|888|84|<sent@example.com>",
            "folder_role": "sent",
        })
        self.index.index_received_attachments(self.account, sent, ["skan_test.pdf"])
        all_documents = self.index.search(self.account)
        self.assertEqual(len(all_documents), 1)
        self.assertEqual(all_documents[0]["source_count"], 2)
        self.assertIn("received", all_documents[0]["source_categories"])
        self.assertIn("sent", all_documents[0]["source_categories"])
        self.assertEqual(len(self.index.search(self.account, category="received")), 1)
        self.assertEqual(len(self.index.search(self.account, category="sent")), 1)

    def test_08c_same_filename_on_different_dates_remains_two_documents(self):
        self.index.index_received_attachments(self.account, self.row, ["formularz.pdf"])
        later = dict(self.row)
        later.update({
            "uid": "mail:later", "server_uid": "99", "message_identity": "INBOX|777|99",
            "progress_id": "INBOX|777|99|<later@example.com>", "date": "28.10.2026, 10:30",
        })
        self.index.index_received_attachments(self.account, later, ["formularz.pdf"])
        self.assertEqual(len(self.index.search(self.account)), 2)

    def test_08d_duplicate_name_from_two_bodystructure_parameters_is_one_document(self):
        self.index.index_received_attachments(
            self.account, self.row, ["Regulamin poczty.pdf", "Regulamin poczty.pdf"]
        )
        values = self.index.search(self.account)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["source_count"], 1)

    def test_08e_damaged_name_missing_polish_letters_uses_the_correct_name(self):
        self.index.index_received_attachments(self.account, self.row, [
            "Informacja o warunkach świadczenia usługi poczty elektronicznej WP.pdf",
            "Informacja o warunkach wiadczenia usugi poczty elektronicznej WP.pdf",
        ])
        values = self.index.search(self.account)
        self.assertEqual(len(values), 1)
        self.assertIn("świadczenia usługi", values[0]["name"])
        self.assertEqual(values[0]["source_count"], 1)

        reopened = DocumentIndex(self.index.path)
        import sqlite3
        db = sqlite3.connect(str(reopened.path))
        try:
            stored_names = [row[0] for row in db.execute("SELECT name FROM documents")]
        finally:
            db.close()
        self.assertEqual(len(stored_names), 2)
        self.assertTrue(all("świadczenia usługi" in name for name in stored_names))

    def test_09_scanned_and_sent_categories_can_describe_same_file(self):
        path = self.make_pdf("podpisany.pdf")
        first = self.index.add_local_document(self.account, path, "scanned", self.row["progress_id"])
        second = self.index.add_local_document(self.account, path, "sent", self.row["progress_id"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.index.search(self.account, category="scanned")), 1)
        self.assertEqual(len(self.index.search(self.account, category="sent")), 1)

    def test_10_document_message_and_task_links_are_stable(self):
        ids = self.index.index_received_attachments(self.account, self.row, ["formularz.pdf"])
        self.index.link_message_tasks(self.row["progress_id"], [("task-sign", "Podpisz formularz")])
        linked = self.index.search(self.account, category="tasks")
        to_sign = self.index.search(self.account, category="to_sign")
        self.assertEqual(linked[0]["id"], ids[0])
        self.assertIn("Podpisz formularz", linked[0]["task_labels"])
        self.assertEqual(len(to_sign), 1)

    def test_11_search_uses_name_person_date_source_and_task(self):
        self.index.index_received_attachments(self.account, self.row, ["formularz.pdf"])
        self.index.link_message_tasks(self.row["progress_id"], [("t1", "Sprawdź podpis")])
        self.assertEqual(len(self.index.search(self.account, "formularz")), 1)
        self.assertEqual(len(self.index.search(self.account, "Kowalska")), 1)
        self.assertEqual(len(self.index.search(self.account, "27.10.2026")), 1)
        self.assertEqual(len(self.index.search(self.account, "podpis")), 1)

    def test_12_index_rebuild_is_atomic_and_integrity_check_is_non_destructive(self):
        path = self.make_pdf("skan.pdf")
        count = self.index.rebuild(self.account, [
            {"source_key": "one", "name": "jeden.pdf", "category": "received"},
            {"source_key": "two", "name": "dwa.pdf", "category": "scanned", "local_path": path},
        ])
        self.assertEqual(count, 2)
        report = self.index.repair(self.account)
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(len(self.index.search(self.account)), 2)

    def test_13_pdf_preview_and_narrow_document_view(self):
        path = create_test_pdf()
        try:
            page = DocumentsPage()
            page.resize(600, 700)
            page.show()
            self.app.processEvents()
            preview = DocumentPreviewPage()
            preview.show()
            self.assertTrue(preview.show_pdf(path, "Dokument testowy.pdf"))
            self.app.processEvents()
            self.assertEqual(page.splitter.orientation(), Qt.Orientation.Vertical)
            self.assertGreaterEqual(preview.pdf_document.pageCount(), 1)
            self.assertTrue(preview.pdf_view.isVisible())
            self.assertIn("Przewijaj", preview.status.text())
            preview.close()
            preview.deleteLater()
            page.close()
            page.deleteLater()
            self.app.processEvents()
        finally:
            if os.path.isfile(path):
                os.unlink(path)


class AdvancedSearchPhaseThreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.index = DocumentIndex(os.path.join(self.temp.name, "search.sqlite3"))
        self.account = "konto@wp.pl"

    def tearDown(self):
        self.temp.cleanup()

    def test_14_message_search_includes_body_text_on_server(self):
        client = MailClient()
        client.connection = FakeFolderIMAP()
        client.load_folder("INBOX", 0, 10, "spotkanie listopad")
        arguments = client.connection.search_calls[-1]
        self.assertIn("TEXT", arguments)

    def test_15_pdf_text_is_extracted_and_searchable(self):
        path = make_searchable_pdf(os.path.join(self.temp.name, "tekst.pdf"))
        text = extract_pdf_text(path)
        self.assertIn("Podpisany formularz", text)
        document_id = self.index.add_local_document(self.account, path, "scanned")
        self.index.update_text(document_id, text)
        values = self.index.search(self.account, "Kowalskiej")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["id"], document_id)

    def test_16_ocr_is_optional_local_and_does_not_change_pdf(self):
        path = make_searchable_pdf(os.path.join(self.temp.name, "skan.pdf"), "Obraz strony")
        original = Path(path).read_bytes()

        def fake_run(args, **_kwargs):
            result = MagicMock()
            result.returncode = 0
            if "--list-langs" in args:
                result.stdout = "List of available languages: pol eng"
                result.stderr = ""
            else:
                result.stdout = "Rozpoznany podpisany skan"
                result.stderr = ""
            return result

        with patch("mailassistant_qt.documents.subprocess.run", side_effect=fake_run):
            availability = local_ocr_available("tesseract-test")
            self.assertTrue(availability["available"])
            text = ocr_pdf(path, "tesseract-test", self.temp.name)
        self.assertIn("Rozpoznany podpisany skan", text)
        self.assertEqual(Path(path).read_bytes(), original)

    def test_17_missing_ocr_has_clear_nonfatal_result(self):
        with patch("mailassistant_qt.documents.shutil.which", return_value=None):
            result = local_ocr_available()
        self.assertFalse(result["available"])
        self.assertIn("Tesseract", result["reason"])
        self.assertEqual(user_error(RuntimeError(result["reason"])), result["reason"])

    def test_17b_digital_pdf_text_does_not_require_tesseract(self):
        path = make_searchable_pdf(os.path.join(self.temp.name, "cyfrowy.pdf"), "Zwykly tekst dokumentu")
        with patch("mailassistant_qt.documents.ocr_pdf", side_effect=AssertionError("OCR nie powinien ruszyć")):
            result = recognize_document_text(path)
        self.assertEqual(result["method"], "pdf_text")
        self.assertIn("Zwykly tekst", result["text"])

    def test_18_natural_language_only_creates_filters_not_results(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "message": {"content": json.dumps({"query": "Kowalska formularz", "category": "received"})}
        }).encode()
        response.__enter__.return_value = response
        with patch("mailassistant_qt.documents.urllib.request.urlopen", return_value=response):
            filters = interpret_natural_query("Znajdź formularz od Kowalskiej")
        self.assertEqual(filters, {"query": "Kowalska formularz", "category": "received"})
        self.assertEqual(self.index.search(self.account, **filters), [])

    def test_19_natural_search_failure_keeps_basic_search_available(self):
        import urllib.error
        with patch("mailassistant_qt.documents.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Zwykłe wyszukiwanie nadal działa"):
                interpret_natural_query("Pokaż skany")
        path = make_searchable_pdf(os.path.join(self.temp.name, "zwykly.pdf"), "Test")
        self.index.add_local_document(self.account, path, "scanned")
        self.assertEqual(len(self.index.search(self.account, "zwykly")), 1)

    def test_20_search_remains_fast_for_one_thousand_documents(self):
        records = [
            {"source_key": f"doc-{index}", "name": f"formularz-{index}.pdf", "category": "received"}
            for index in range(1000)
        ]
        self.index.rebuild(self.account, records)
        started = time.perf_counter()
        values = self.index.search(self.account, "formularz-999")
        elapsed = time.perf_counter() - started
        self.assertEqual(len(values), 1)
        self.assertLess(elapsed, 1.0)

    def test_21_last_month_query_gets_a_deterministic_date_filter(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "message": {"content": json.dumps({"query": "Kowalska", "category": "all"})}
        }).encode()
        response.__enter__.return_value = response
        with patch("mailassistant_qt.documents.urllib.request.urlopen", return_value=response):
            filters = interpret_natural_query("Pokaż dokumenty Kowalskiej z zeszłego miesiąca")
        now = __import__("datetime").datetime.now()
        previous_month = now.month - 1 or 12
        previous_year = now.year - 1 if now.month == 1 else now.year
        self.assertIn(f"{previous_month:02d}.{previous_year:04d}", filters["query"])


if __name__ == "__main__":
    unittest.main()
