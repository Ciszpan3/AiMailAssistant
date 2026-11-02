import io
import json
import unittest
from unittest.mock import patch
import urllib.error

import ai_assistant_04a as app


SAMPLE_BODY = (
    'Formularz należy wypełnić elektronicznie, bez drukowania. '
    'Proszę przesłać skan podpisanego formularza do 29.10.2026 do godz. 16:00. '
    'Udział w spotkaniu proszę potwierdzić do 30.10.2026 do godz. 12:00. '
    'Spotkanie rozpocznie się 03.11.2026 o 10:30. '
    'Jeśli bierze Pani udział, należy przynieść dwa egzemplarze formularza.'
)

COMPLEX_BODY = (
    'Dzień dobry, W związku z przygotowaniami do spotkania organizacyjnego proszę o wykonanie '
    'poniższych czynności. Część z nich zależy od wcześniejszego wykonania innych, dlatego proszę '
    'zwrócić uwagę na kolejność. 1. Dokumenty do podpisania W załączniku znajduje się plik '
    'Formularz_zgloszeniowy.pdf. Proszę go pobrać i wydrukować w dwóch egzemplarzach. Następnie '
    'należy uzupełnić na obu egzemplarzach imię, nazwisko, adres korespondencyjny i numer telefonu, '
    'a potem podpisać oba dokumenty odręcznie. Po podpisaniu proszę zeskanować wszystkie strony '
    'pierwszego egzemplarza do jednego pliku PDF. Skan powinien być czytelny, w kolorze i zawierać '
    'wszystkie podpisy. Drugi, papierowy egzemplarz należy zachować do przekazania podczas spotkania. '
    'Podpisany skan proszę odesłać w odpowiedzi na tę wiadomość najpóźniej do 29 października 2026 r. '
    'do godziny 16:00. 2. Weryfikacja danych Przed odesłaniem skanu proszę sprawdzić, czy adres '
    'korespondencyjny wpisany w formularzu jest aktualny. Jeśli adres się zmienił, proszę dodatkowo '
    'napisać o tym w treści odpowiedzi mailowej. Jeśli adres pozostaje bez zmian, nie trzeba o tym '
    'wspominać. Proszę nie przesyłać zdjęć dowodu osobistego ani innych dokumentów tożsamości. '
    '3. Potwierdzenie spotkania Spotkanie organizacyjne odbędzie się 3 listopada 2026 r. o godzinie '
    '10:30 w sali konferencyjnej nr 4. Proszę potwierdzić udział w spotkaniu do 30 października '
    '2026 r. do godziny 12:00. Jeżeli nie mogą Państwo uczestniczyć, proszę zamiast potwierdzenia '
    'zaproponować dwa inne terminy w pierwszym tygodniu listopada. Na spotkanie należy przynieść '
    'drugi, podpisany egzemplarz formularza. 4. Instrukcja dodatkowa Drugi załącznik, Instrukcja.pdf, '
    'zawiera informacje organizacyjne. Proszę go przeczytać przed spotkaniem. Nie trzeba go drukować '
    'ani odsyłać. 5. Ważne informacje W ubiegłym roku formularze należało odesłać do 15 października, '
    'ale ten termin już nie obowiązuje. Aktualny termin podano w punkcie pierwszym. Nie trzeba '
    'kontaktować się telefonicznie, jeśli wszystkie dokumenty zostaną przesłane prawidłowo.'
)


def valid_result():
    return {
        'summary': 'Wiadomość zawiera przygotowania do spotkania i trzy osobne terminy.',
        'events': [
            {
                'id': 'event-1',
                'title': 'Spotkanie',
                'start': '2026-11-03T10:30',
                'location': '',
                'condition': '',
                'confidence': 'high',
                'source_quote': 'Spotkanie rozpocznie się 03.11.2026 o 10:30.'
            }
        ],
        'tasks': [
            {
                'id': 'task-1',
                'action': 'Wypełnić formularz elektronicznie',
                'deadline': '',
                'quantity': 0,
                'condition': '',
                'instruction': 'Bez drukowania',
                'depends_on': [],
                'confidence': 'high',
                'source_quote': 'Formularz należy wypełnić elektronicznie, bez drukowania.'
            },
            {
                'id': 'task-2',
                'action': 'Przesłać skan podpisanego formularza',
                'deadline': '2026-10-29T16:00',
                'quantity': 0,
                'condition': '',
                'instruction': '',
                'depends_on': ['task-1'],
                'confidence': 'high',
                'source_quote': 'Proszę przesłać skan podpisanego formularza do 29.10.2026 do godz. 16:00.'
            },
            {
                'id': 'task-3',
                'action': 'Potwierdzić udział w spotkaniu',
                'deadline': '2026-10-30T12:00',
                'quantity': 0,
                'condition': '',
                'instruction': '',
                'depends_on': [],
                'confidence': 'high',
                'source_quote': 'Udział w spotkaniu proszę potwierdzić do 30.10.2026 do godz. 12:00.'
            },
            {
                'id': 'task-4',
                'action': 'Przynieść formularze na spotkanie',
                'deadline': '',
                'quantity': 2,
                'condition': 'Tylko w przypadku udziału w spotkaniu',
                'instruction': '',
                'depends_on': ['task-3', 'event-1'],
                'confidence': 'high',
                'source_quote': 'Jeśli bierze Pani udział, należy przynieść dwa egzemplarze formularza.'
            }
        ],
        'warnings': [],
        'completeness': {'status': 'complete', 'missing': [], 'conflicts': []}
    }


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class AnalysisValidationTests(unittest.TestCase):
    def test_sample_keeps_action_deadlines_separate_from_event(self):
        result = app.validate_analysis(valid_result(), SAMPLE_BODY)
        deadlines = {task['id']: task['deadline'] for task in result['tasks']}
        self.assertEqual(deadlines['task-2'], '2026-10-29T16:00')
        self.assertEqual(deadlines['task-3'], '2026-10-30T12:00')
        self.assertEqual(result['events'][0]['start'], '2026-11-03T10:30')
        self.assertEqual(result['tasks'][3]['quantity'], 2)
        self.assertIn('udział', result['tasks'][3]['condition'])
        self.assertEqual(result['tasks'][0]['instruction'], 'Bez drukowania')

    def test_unmatched_quote_becomes_warning_instead_of_error(self):
        data = valid_result()
        data['tasks'][0]['source_quote'] = 'Fragment wymyślony przez model.'
        result = app.validate_analysis(data, SAMPLE_BODY)
        self.assertTrue(any('dopasować fragmentu źródłowego' in item for item in result['_technical_notes']))

    def test_accepts_small_quote_change_and_uses_real_sentence(self):
        data = valid_result()
        data['tasks'][1]['source_quote'] = (
            'Proszę o przesłanie skanu podpisanego formularza do 29-10-2026, godzina 16:00.'
        )
        result = app.validate_analysis(data, SAMPLE_BODY)
        self.assertEqual(
            result['tasks'][1]['source_quote'],
            'Proszę przesłać skan podpisanego formularza do 29.10.2026 do godz. 16:00.'
        )

    def test_incomplete_json_uses_safe_completeness_default(self):
        data = valid_result()
        del data['completeness']
        result = app.validate_analysis(data, SAMPLE_BODY)
        self.assertEqual(result['completeness']['status'], 'uncertain')

    def test_unknown_dependency_is_removed_instead_of_error(self):
        data = valid_result()
        data['tasks'][0]['depends_on'] = ['task-999']
        result = app.validate_analysis(data, SAMPLE_BODY)
        self.assertEqual(result['tasks'][0]['depends_on'], [])
        self.assertTrue(any('nieistniejącą zależność' in item for item in result['_technical_notes']))

    def test_written_month_and_wrong_event_quote_do_not_stop_analysis(self):
        body = (
            'Spotkanie rozpocznie się 3 listopada 2026 o 10:30. '
            'Podpisany skan proszę odesłać do 29 października 2026 o 16:00.'
        )
        data = {
            'summary': 'Informacja o spotkaniu i skanie.',
            'events': [{
                'id': 'event-1', 'title': 'Spotkanie', 'start': '2026-11-03T10:30',
                'location': '', 'condition': '', 'confidence': 'high',
                'source_quote': 'Proszę pamiętać o spotkaniu.'
            }],
            'tasks': [{
                'id': 'task-1', 'action': 'Odesłać podpisany skan',
                'deadline': '2026-10-29T16:00', 'quantity': 0,
                'condition': '', 'instruction': '', 'depends_on': [],
                'confidence': 'high', 'source_quote': 'Podpisany skan proszę odesłać.'
            }],
            'warnings': [],
            'completeness': {'status': 'complete', 'missing': [], 'conflicts': []}
        }
        result = app.validate_analysis(data, body)
        self.assertEqual(result['events'][0]['start'], '2026-11-03T10:30')
        self.assertIn('3 listopada 2026', result['events'][0]['source_quote'])
        self.assertEqual(result['tasks'][0]['deadline'], '2026-10-29T16:00')

    def test_complex_mail_dates_are_classified_correctly(self):
        evidence = app.extract_date_evidence(COMPLEX_BODY)
        classified = {item['value']: item['classification'] for item in evidence if item['value']}
        self.assertTrue(classified['2026-11-03T10:30'].startswith('event'))
        self.assertTrue(classified['2026-10-29T16:00'].startswith('task'))
        self.assertTrue(classified['2026-10-30T12:00'].startswith('task'))

    def test_complex_mail_gets_complete_plain_language_workflow(self):
        minimal_model_result = {
            'summary': 'Instrukcje przygotowania do spotkania.',
            'events': [], 'tasks': [], 'warnings': [],
            'completeness': {'status': 'uncertain', 'missing': [], 'conflicts': []}
        }
        result = app.validate_analysis(minimal_model_result, COMPLEX_BODY)
        actions = [task['action'] for task in result['tasks']]
        self.assertEqual(len(actions), 9)
        self.assertTrue(any('wydrukować 2 egzemplarze' in action for action in actions))
        self.assertTrue(any('Podpisać odręcznie' in action for action in actions))
        self.assertTrue(any('Zeskanować wszystkie strony' in action for action in actions))
        self.assertTrue(any('adres korespondencyjny' in action for action in actions))
        self.assertTrue(any('Instrukcja.pdf' in action for action in actions))
        self.assertTrue(any('drugi podpisany egzemplarz' in action for action in actions))
        deadlines = {task['deadline'] for task in result['tasks'] if task['deadline']}
        self.assertEqual(deadlines, {'2026-10-29T16:00', '2026-10-30T12:00'})
        self.assertEqual(result['events'][0]['start'], '2026-11-03T10:30')
        self.assertEqual(result['events'][0]['location'], 'sala konferencyjna nr 4')
        self.assertEqual(len(actions), len(set(actions)))
        self.assertFalse(any('zgodnie z wiadomością' in action for action in actions))
        task_ids = {task['id'] for task in result['tasks']}
        self.assertTrue(all(
            dependency in task_ids
            for task in result['tasks'] for dependency in task['depends_on']
        ))
        self.assertFalse(any('walidator' in item.lower() for item in result['warnings']))
        self.assertIn('29.10.2026 16:00', result['summary'])
        self.assertIn('03.11.2026 10:30', result['summary'])

    def test_analysis_parses_and_validates_ollama_json(self):
        outer = {'message': {'content': json.dumps(valid_result(), ensure_ascii=False)}}
        response = FakeResponse(json.dumps(outer, ensure_ascii=False).encode('utf-8'))
        with patch('urllib.request.urlopen', return_value=response):
            result = app.analyze_email('Organizacja spotkania', SAMPLE_BODY)
        self.assertEqual(len(result['tasks']), 4)
        self.assertEqual(result['events'][0]['start'], '2026-11-03T10:30')

    def test_connection_error_has_safe_message(self):
        with patch(
            'urllib.request.urlopen',
            side_effect=urllib.error.URLError('offline')
        ):
            with self.assertRaisesRegex(RuntimeError, 'Nie można połączyć się z Ollama'):
                app.analyze_email('Temat', SAMPLE_BODY)

    def test_two_invalid_json_responses_return_fallback_plan(self):
        invalid_outer = {'message': {'content': 'to nie jest JSON'}}
        responses = [
            FakeResponse(json.dumps(invalid_outer).encode('utf-8')),
            FakeResponse(json.dumps(invalid_outer).encode('utf-8'))
        ]
        with patch('urllib.request.urlopen', side_effect=responses):
            result = app.analyze_email('Organizacja spotkania', SAMPLE_BODY)
        self.assertEqual(result['completeness']['status'], 'uncertain')
        self.assertIn('planu awaryjnego', result['warnings'][0])
        self.assertIn('2026-11-03T10:30', [event['start'] for event in result['events']])
        self.assertIn('2026-10-30T12:00', [task['deadline'] for task in result['tasks']])

    def test_prompt_explicitly_separates_dates(self):
        payload = app.build_analysis_payload('Temat', SAMPLE_BODY)
        prompt = payload['messages'][1]['content']
        self.assertIn('termin wykonania czynności i data wydarzenia to różne rzeczy', prompt)
        self.assertIn('Nie przypisuj daty spotkania do potwierdzenia udziału', prompt)


if __name__ == '__main__':
    unittest.main()
