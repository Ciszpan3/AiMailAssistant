import math
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mailassistant_qt.semantic_search import (
    OllamaEmbedder, OllamaSearchVerifier, SemanticMailIndex, SemanticUnavailableError,
    _fold, _is_negative_request, _query_requires_request, _request_evidence_quote,
    _validated_source_quote, _verification_query, chunk_text, evaluate_cases,
)
from mailassistant_qt.semantic_ui import SemanticSearchPage


class ControlledPolishEmbedder:
    """Deterministyczny substytut modelu używany wyłącznie w testach offline."""
    model = "controlled-polish-test"

    concepts = (
        ("projekt", "zadani", "prac", "stud", "ocen", "wykona", "sztuczn"),
        ("samoch", "auto", "pojazd", "napraw"),
        ("dokument", "formular", "papier", "podpis", "zaświadc"),
        ("spotka", "zebran", "rozmow", "przyjść", "obecno", "szkolen"),
        ("płat", "faktur", "rachun", "opłat", "koszt"),
        ("chcia", "proszę", "prosil", "prosił", "żąda", "oczekuje"),
        ("wakac", "przepis", "pogod", "newsletter", "promoc"),
    )

    def embed(self, texts):
        result = []
        for text in texts:
            lowered = str(text).casefold().replace("studja", "studia").replace("wiadomosc", "wiadomość")
            vector = [float(sum(lowered.count(word) for word in group)) for group in self.concepts]
            vector.append(0.15)
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            result.append([value / norm for value in vector])
        return result


class MissingEmbedder:
    model = "missing"

    def embed(self, _texts):
        raise SemanticUnavailableError("Ollama jest niedostępna.")


class LowRankAnchorEmbedder:
    model = "low-rank-anchor-test"

    def embed(self, texts):
        vectors = []
        for text in texts:
            value = str(text).casefold()
            if value.startswith("task: search result"):
                vectors.append([1.0, 0.0])
            elif "anna kowalska" in value:
                vectors.append([0.20, math.sqrt(0.96)])
            else:
                vectors.append([0.90, math.sqrt(0.19)])
        return vectors


class StrictVerifier:
    def analyze_query(self, query):
        people = [name for name in ("kowalska", "nowak") if name in query.casefold()]
        return {
            "people": people, "emails": [], "dates": [], "numbers": [],
            "quoted_phrases": [], "intent": query,
        }

    def verify(self, query, candidates):
        results = {}
        for candidate in candidates:
            text = str(candidate.get("verification_text") or "")
            if "kowalska" in text.casefold() and "proszę przesłać formularz" in text.casefold():
                results[str(candidate["message_key"])] = {
                    "quote": "Proszę przesłać formularz",
                    "reason": "Anna Kowalska prosi o przesłanie formularza.",
                }
        return results


class BrokenVerifier(StrictVerifier):
    def verify(self, query, candidates):
        raise SemanticUnavailableError("Kontrola jest niedostępna.")


def row(identifier, subject):
    return {
        "uid": f"mail:{identifier}", "server_uid": identifier, "folder": "INBOX",
        "uid_validity": "77", "message_identity": f"INBOX|77|{identifier}",
        "progress_id": f"INBOX|77|{identifier}|<{identifier}@test>",
        "sender": "nadawca@example.test", "recipient": "ja@example.test",
        "subject": subject, "date": "20.10.2026, 12:00",
    }


class SemanticSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=".")
        self.index = SemanticMailIndex(Path(self.temporary.name) / "semantic.sqlite3")
        self.embedder = ControlledPolishEmbedder()
        self.account = "test@wp.pl"
        self.messages = {
            "project": (
                "Ważne zadanie na studia",
                "Musisz przygotować projekt dotyczący sztucznej inteligencji. Od wykonania projektu zależy ocena końcowa na studiach.",
            ),
            "car": ("Faktura za naprawę", "W załączniku przesyłam fakturę za naprawę samochodu."),
            "document": ("Formularz", "Proszę podpisać formularz i przesłać dokument do piątku."),
            "meeting": ("Termin zebrania", "Spotkanie organizacyjne odbędzie się we wtorek. Proszę potwierdzić obecność."),
            "payment": ("Opłata", "Rachunek za energię należy opłacić do końca miesiąca."),
            "noise": ("Newsletter", "Promocje wakacyjne, prognoza pogody i nowy przepis kulinarny."),
        }
        self.keys = {}
        for number, (identifier, (subject, body)) in enumerate(self.messages.items(), 1):
            update = self.index.upsert_message(self.account, row(str(number), subject), body, self.embedder)
            self.keys[identifier] = update.message_key

    def tearDown(self):
        self.temporary.cleanup()

    def result_keys(self, query, limit=5):
        result = self.index.search(self.account, query, self.embedder, limit=limit)
        return result, [item["message_key"] for item in result["results"]]

    def test_a_project_on_studies_is_first(self):
        _result, keys = self.result_keys("Znajdź wiadomość o jakimś projekcie, który muszę wykonać")
        self.assertEqual(keys[0], self.keys["project"])

    def test_b_very_general_work_description_returns_project(self):
        _result, keys = self.result_keys("Było coś o pracy, którą muszę zrobić")
        self.assertIn(self.keys["project"], keys)

    def test_c_different_car_vocabulary(self):
        _result, keys = self.result_keys("koszty naprawy auta")
        self.assertEqual(keys[0], self.keys["car"])

    def test_d_document_described_as_paper(self):
        _result, keys = self.result_keys("Ktoś chciał ode mnie jakiś papier")
        self.assertEqual(keys[0], self.keys["document"])

    def test_e_meeting_described_as_conversation(self):
        _result, keys = self.result_keys("mail o rozmowie, na którą miałem przyjść")
        self.assertEqual(keys[0], self.keys["meeting"])

    def test_f_typo_does_not_break_semantic_search(self):
        _result, keys = self.result_keys("znajdz wiadomosc o projekcie na studja")
        self.assertEqual(keys[0], self.keys["project"])

    def test_g_middle_of_long_mail_is_searchable_as_its_own_chunk(self):
        body = ("Zwykła informacja. " * 500) + "Proszę przynieść podpisane zaświadczenie lekarskie." + (" Dalsza treść." * 500)
        update = self.index.upsert_message(self.account, row("99", "Długa wiadomość"), body, self.embedder)
        self.assertGreater(update.chunks, 3)
        _result, keys = self.result_keys("ktoś prosił mnie o podpisany papier")
        self.assertIn(update.message_key, keys)

    def test_h_unrelated_mail_does_not_dominate(self):
        _result, keys = self.result_keys("projekt do wykonania na ocenę")
        self.assertEqual(keys[0], self.keys["project"])
        self.assertNotEqual(keys[0], self.keys["noise"])

    def test_i_incomplete_index_is_reported(self):
        self.index.set_complete(self.account, False, 100)
        result, _keys = self.result_keys("spotkanie")
        self.assertFalse(result["complete"])
        self.assertEqual(result["expected"], 100)

    def test_j_missing_ollama_keeps_explicit_text_fallback(self):
        result = self.index.search(self.account, "formularz", MissingEmbedder(), limit=5)
        self.assertFalse(result["semantic_available"])
        self.assertTrue(result["semantic_error"])
        self.assertEqual(result["results"][0]["message_key"], self.keys["document"])

    def test_incremental_index_skips_unchanged_mail_and_survives_restart(self):
        same = self.index.upsert_message(
            self.account, row("1", self.messages["project"][0]), self.messages["project"][1], self.embedder
        )
        self.assertFalse(same.changed)
        reopened = SemanticMailIndex(self.index.path)
        self.assertEqual(reopened.status(self.account)["indexed"], len(self.messages))

    def test_chunking_removes_technical_mime_and_excess_quotes(self):
        chunks = chunk_text("Temat", "Content-Type: text/plain\nWłaściwa wiadomość\n> cytat\n> cytat 2\n> cytat 3")
        joined = " ".join(chunks)
        self.assertIn("Właściwa wiadomość", joined)
        self.assertNotIn("Content-Type", joined)
        self.assertNotIn("cytat 3", joined)

    def test_results_contain_real_quote_and_stable_server_reference(self):
        result, _keys = self.result_keys("auto i koszt naprawy")
        first = result["results"][0]
        self.assertIn("samochodu", first["snippet"])
        self.assertEqual(first["server_uid"], "2")
        self.assertNotIn("cosine", str(first).casefold())

    def test_quality_metrics_and_resource_bounds(self):
        cases = [
            ("projekt, który mam zrobić", self.keys["project"]),
            ("koszt naprawy auta", self.keys["car"]),
            ("ktoś prosił o papier", self.keys["document"]),
            ("rozmowa na którą mam przyjść", self.keys["meeting"]),
            ("jakaś płatność", self.keys["payment"]),
        ]
        tracemalloc.start()
        started = time.perf_counter()
        metrics = evaluate_cases(self.index, self.account, cases, self.embedder)
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(metrics["recall_at_5"], 1.0)
        self.assertGreaterEqual(metrics["mrr"], 0.9)
        self.assertLess(elapsed, 2.0)
        self.assertLess(peak, 20 * 1024 * 1024)

    def test_ui_has_plain_description_mode_and_real_result_button(self):
        page = SemanticSearchPage()
        self.assertIn("własnymi słowami", page.query.placeholderText())
        result, _keys = self.result_keys("formularz")
        page.show_results(result)
        widget = page.results.itemWidget(page.results.item(0))
        buttons = widget.findChildren(type(page.search_button))
        self.assertTrue(any(button.text() == "Otwórz wiadomość" for button in buttons))
        page.deleteLater()

    def test_strict_person_and_intent_filter_rejects_unrelated_mail(self):
        correct = self.index.upsert_message(
            self.account, row("201", "Dokumenty od Anny"),
            "Dzień dobry. Proszę przesłać formularz do piątku. Pozdrawiam, Anna Kowalska.",
            self.embedder,
        )
        unrelated = self.index.upsert_message(
            self.account, row("202", "Luźna informacja"),
            "Wczoraj spotkałem Annę Kowalską. To tylko neutralna informacja.",
            self.embedder,
        )
        for query in (
            "kowalska coś chciała ode mnie",
            "kowalska coś ode mnie chciała",
            "nazwisko kowalska cos ode mnie chciała",
        ):
            result = self.index.search(
                self.account, query, self.embedder, limit=10, verifier=StrictVerifier(),
            )
            keys = [item["message_key"] for item in result["results"]]
            self.assertEqual(keys, [correct.message_key], query)
            self.assertNotIn(unrelated.message_key, keys)
            self.assertTrue(all(item["label"] != "Luźno powiązana" for item in result["results"]))

    def test_missing_person_returns_no_results_instead_of_best_weak_mail(self):
        result = self.index.search(
            self.account, "nowak coś chciał ode mnie", self.embedder,
            limit=10, verifier=StrictVerifier(),
        )
        self.assertEqual(result["results"], [])

    def test_broken_verifier_does_not_release_ambiguous_person_match(self):
        self.index.upsert_message(
            self.account, row("203", "Informacja o Kowalskiej"),
            "Anna Kowalska napisała: Proszę potwierdzić obecność na spotkaniu.", self.embedder,
        )
        result = self.index.search(
            self.account, "kowalska coś chciała ode mnie", self.embedder,
            limit=10, verifier=BrokenVerifier(),
        )
        self.assertEqual(result["results"], [])
        self.assertTrue(result["verification_notice"])

    def test_candidate_pool_is_adaptive_and_bounded(self):
        self.assertEqual(self.index.semantic_candidate_limit(100), 30)
        self.assertEqual(self.index.semantic_candidate_limit(500), 30)
        self.assertEqual(self.index.semantic_candidate_limit(1000), 30)
        self.assertEqual(self.index.semantic_candidate_limit(2500), 40)
        self.assertEqual(self.index.semantic_candidate_limit(10000), 80)

    def test_same_message_id_is_shown_only_once(self):
        first = row("301", "Duplikat faktury")
        second = row("302", "Duplikat faktury")
        first["message_id"] = second["message_id"] = "<same-message@test>"
        body = "W załączniku jest faktura za naprawę samochodu."
        self.index.upsert_message(self.account, first, body, self.embedder)
        self.index.upsert_message(self.account, second, body, self.embedder)
        result = self.index.search(
            self.account, "faktura za naprawę samochodu", self.embedder, limit=5
        )
        duplicates = [item for item in result["results"] if item["subject"] == "Duplikat faktury"]
        self.assertEqual(len(duplicates), 1)

    def test_exact_name_is_checked_even_below_top_thirty_semantic_candidates(self):
        embedder = LowRankAnchorEmbedder()
        for number in range(40):
            self.index.upsert_message(
                self.account, row(f"low-{number}", f"Niepowiązany {number}"),
                "Zwykła wiadomość bez wskazanej osoby.", embedder,
            )
        correct = self.index.upsert_message(
            self.account, row("low-correct", "Właściwa wiadomość"),
            "Proszę przesłać formularz. Pozdrawiam, Anna Kowalska.", embedder,
        )
        result = self.index.search(
            self.account, "kowalska coś ode mnie chciała", embedder,
            limit=5, verifier=StrictVerifier(),
        )
        self.assertEqual(
            [item["message_key"] for item in result["results"]], [correct.message_key]
        )

    def test_polish_word_order_and_l_stroke_are_normalized_for_request_intent(self):
        query = "kowalska coś ode mnie chciała"
        self.assertEqual(_fold("chciała"), "chciala")
        clarified = _verification_query(query, {"people": ["kowalska"]})
        self.assertIn("Jednoznaczne znaczenie", clarified)

    def test_spliced_ai_quote_is_replaced_with_real_relevant_sentence(self):
        source = (
            "Spotkanie organizacyjne odbędzie się we wtorek. "
            "Pomiędzy zdaniami jest inna treść. Dokument należy podpisać."
        )
        proposed = "Spotkanie organizacyjne odbędzie się we wtorek. Dokument należy podpisać."
        quote = _validated_source_quote(proposed, source, "szukam wiadomości o spotkaniu")
        self.assertEqual(quote, "Spotkanie organizacyjne odbędzie się we wtorek.")

    def test_negative_instruction_is_not_treated_as_requested_document(self):
        self.assertTrue(_is_negative_request("Proszę nie przesyłać zdjęć dowodu."))
        self.assertFalse(_is_negative_request("Proszę przesłać podpisany formularz."))

    def test_distinct_reply_and_original_can_both_be_shown(self):
        original = row("thread-original", "Prośba o formularz")
        reply = row("thread-reply", "Re: Prośba o formularz")
        self.index.upsert_message(
            self.account, original,
            "Proszę przesłać dokument i podpisać formularz.", self.embedder,
        )
        self.index.upsert_message(
            self.account, reply,
            "W załączeniu przesyłam dokument i formularz.", self.embedder,
        )
        result = self.index.search(
            self.account, "dokument formularz", self.embedder, limit=5
        )
        subjects = [item["subject"] for item in result["results"]]
        self.assertIn("Prośba o formularz", subjects)
        self.assertIn("Re: Prośba o formularz", subjects)

    def test_several_distinct_relevant_messages_are_returned(self):
        expected = []
        for number, body in enumerate((
            "Proszę uzupełnić formularz zgłoszeniowy.",
            "W formularzu należy wpisać aktualny adres.",
            "Podpisany formularz proszę przynieść na spotkanie.",
        ), 1):
            update = self.index.upsert_message(
                self.account,
                row(f"many-{number}", f"Formularz — wiadomość {number}"),
                body,
                self.embedder,
            )
            expected.append(update.message_key)

        result = self.index.search(self.account, "formularz", self.embedder, limit=5)
        keys = {item["message_key"] for item in result["results"]}
        self.assertTrue(set(expected).issubset(keys))

    def test_word_inflection_is_found_by_text_ranking(self):
        update = self.index.upsert_message(
            self.account,
            row("inflection", "Dane zgłoszeniowe"),
            "W formularzu należy podać numer telefonu.",
            self.embedder,
        )
        result = self.index.search(self.account, "formularz", MissingEmbedder(), limit=5)
        self.assertIn(update.message_key, [item["message_key"] for item in result["results"]])

    def test_request_word_inflection_is_really_enforced(self):
        self.assertTrue(_query_requires_request("kowalska coś ode mnie chciała"))
        self.assertTrue(_query_requires_request("kowalski prosił czegoś ode mnie"))
        self.assertFalse(_query_requires_request("wiadomość wspominała Kowalską"))

    def test_exact_verified_quote_is_not_replaced_by_another_request(self):
        source = (
            "Proszę zeskanować pierwszą stronę formularza. "
            "Proszę potwierdzić udział w spotkaniu do piątku."
        )
        proposed = "Proszę potwierdzić udział w spotkaniu do piątku."
        self.assertEqual(
            _validated_source_quote(proposed, source, "kowalska coś ode mnie chciała"),
            proposed,
        )

    def test_text_fallback_with_person_still_requires_real_request(self):
        correct = self.index.upsert_message(
            self.account, row("fallback-correct", "Prośba Anny"),
            "Proszę wysłać formularz. Pozdrawiam, Anna Kowalska.", self.embedder,
        )
        unrelated = self.index.upsert_message(
            self.account, row("fallback-wrong", "Wzmianka o Annie"),
            "Wczoraj widziałem Annę Kowalską. To wyłącznie neutralna informacja.", self.embedder,
        )
        result = self.index.search(
            self.account, "kowalska coś ode mnie chciała", MissingEmbedder(),
            limit=5, verifier=BrokenVerifier(),
        )
        keys = [item["message_key"] for item in result["results"]]
        self.assertIn(correct.message_key, keys)
        self.assertNotIn(unrelated.message_key, keys)
        self.assertTrue(_request_evidence_quote("Proszę wysłać formularz."))

    def test_exact_surname_skips_redundant_query_analysis(self):
        class CountingVerifier(StrictVerifier):
            def __init__(self):
                self.analysis_calls = 0

            def analyze_query(self, query):
                self.analysis_calls += 1
                return super().analyze_query(query)

        self.index.upsert_message(
            self.account, row("counting", "Prośba Kowalskiej"),
            "Proszę przesłać formularz. Pozdrawiam, Anna Kowalska.", self.embedder,
        )
        verifier = CountingVerifier()
        self.index.search(
            self.account, "kowalska coś ode mnie chciała", self.embedder,
            limit=5, verifier=verifier,
        )
        self.assertEqual(verifier.analysis_calls, 0)

    def test_local_model_timeouts_are_bounded(self):
        self.assertEqual(OllamaSearchVerifier().timeout, 45)
        self.assertEqual(OllamaEmbedder().timeout, 45)

    def test_result_card_fits_narrow_low_window_and_keeps_open_button_near_top(self):
        page = SemanticSearchPage()
        page.resize(760, 680)
        page.show()
        page.show_results({
            "results": [{
                "label": "Bardzo podobna", "date": "30.10.2026", "subject": "Długi temat " * 8,
                "sender": "Bardzo długi nadawca <nadawca@example.com>",
                "explanation": "Rzeczywista prośba potwierdzająca dopasowanie.",
                "snippet": "Bardzo długi cytat z wiadomości. " * 20,
            }],
            "indexed": 11, "complete": False,
        })
        self.app.processEvents()
        item = page.results.item(0)
        widget = page.results.itemWidget(item)
        required = widget.layout().heightForWidth(widget.width())
        self.assertGreaterEqual(item.sizeHint().height(), required)
        open_button = next(
            button for button in widget.findChildren(type(page.search_button))
            if button.text() == "Otwórz wiadomość"
        )
        # Przycisk mieści się w pierwszych 230 px karty, czyli w widocznym
        # obszarze listy nawet na niskim ekranie.
        self.assertLess(open_button.geometry().bottom(), 230)
        self.assertLessEqual(page.query.maximumHeight(), 104)
        self.assertIn("Starsze wiadomości nie są jeszcze przygotowane", page.status.text())
        self.assertNotIn("Trwa przygotowywanie", page.status.text())
        page.close()
        page.deleteLater()

    def test_incomplete_status_only_claims_active_preparation_when_it_is_active(self):
        page = SemanticSearchPage()
        page.set_indexing(True)
        page.show_results({"results": [], "indexed": 3, "complete": False})
        self.assertIn("Trwa przygotowywanie", page.status.text())
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
