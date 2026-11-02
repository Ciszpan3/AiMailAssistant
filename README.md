# Asystent Poczty — PySide6, Ollama, urządzenia i bezpieczne odpowiedzi

Aplikacja pobiera wiadomości z WP Poczty w trybie tylko do odczytu, pokazuje treść i załączniki PDF, a lokalna Ollama (`qwen2.5:3b`) przygotowuje prosty plan działania. Interfejs jest przeznaczony również dla osoby, która nie czuje się pewnie przy komputerze.

`ai_assistant_04a.py` pozostaje zachowany jako punkt odniesienia Testu 04A.1. Nowa aplikacja nie importuje tkinter ani starego interfejsu.

## Instalacja na Windows

W PowerShell, w katalogu projektu:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Projekt używa `PySide6 6.12.0` oraz, tylko w Windows, `pywin32 312` do obsługi WIA. Obsługiwany Python: 3.10–3.15.

Aktywacja środowiska jest opcjonalna. Jeżeli chcesz używać krótszych poleceń:

```powershell
.\.venv\Scripts\Activate.ps1
python mail_assistant_qt.py
```

Jeżeli PowerShell blokuje skrypt aktywujący, w bieżącym oknie można wykonać:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Uruchomienie

Najpierw upewnij się, że Ollama i model są dostępne:

```powershell
ollama pull qwen2.5:3b
ollama pull embeddinggemma:300m
ollama serve
```

`qwen2.5:3b` przygotowuje plany i odpowiedzi. Osobny, mniejszy model
`embeddinggemma:300m` służy wyłącznie do lokalnego wyszukiwania wiadomości po
ich znaczeniu. Wymaga Ollamy 0.11.10 lub nowszej.

Następnie:

```powershell
.\.venv\Scripts\python.exe mail_assistant_qt.py
```

W **Ustawieniach** wpisz adres WP Poczty i hasło. Hasło jest przechowywane tylko w pamięci bieżącej sesji. Zapamiętany adres nie oznacza zalogowania: wiadomości, zadania, dokumenty i odpowiedzi są ukryte aż do udanego połączenia z WP Pocztą.

## Co działa

- prosty ekran startowy z czterema dużymi czynnościami, podsumowaniem i kontynuowaniem ostatniego zadania;
- spójna nawigacja: Start, Poczta, Moje zadania, Moje dokumenty, Szukaj i Pomoc; Ustawienia są oddzielone od codziennych czynności;
- wspólny dostępny system wyglądu z wyraźnym fokusem klawiatury, hierarchią przycisków i prostymi stanami sukcesu, ostrzeżenia oraz błędu;
- domyślny tryb prosty dla starszej osoby oraz tryb zaawansowany z pełnymi ustawieniami i diagnostyką;
- sześciokrokowy kreator pierwszego uruchomienia sprawdzający konto WP, lokalną Ollamę, model oraz dostępne urządzenia;
- krótka pomoc dopasowana do aktualnego ekranu i czytelniejsze prowadzenie zadań krok po kroku;
- centrum poczty z rzeczywistymi folderami konta IMAP i czytelną listą wiadomości; pełna treść otwiera się na osobnej, przestronnej stronie z przyciskiem powrotu;
- kompaktowy widok Poczty na małym ekranie: wybór folderu zastępuje ciasny panel, a dodatkowe filtry można rozwinąć bez odbierania miejsca wiadomościom;
- ręczne **Odśwież pocztę**, które wykrywa nowe wiadomości bez ponownego wpisywania hasła i nie tworzy duplikatów;
- czytelny ekran wymagający logowania we wszystkich zakładkach zawierających dane konta;
- foldery Odebrane, Wysłane, Szkice, Archiwum, Kosz i własne foldery są rozpoznawane z serwera, a nie wpisane na stałe;
- stronicowanie starszej poczty, filtrowanie wiadomości nieprzeczytanych i z załącznikami oraz wyszukiwanie na serwerze także w treści maili i w całej skrzynce;
- osobny ekran **Szukaj po opisie wiadomości**, który odnajduje mail po jego sensie nawet bez znajomości tematu, daty i dokładnych słów;
- lokalne embeddingi `embeddinggemma:300m`, dzielenie długich maili na fragmenty oraz hybrydowy ranking znaczenia i zwykłego tekstu;
- rygorystyczna kontrola wyników: nazwiska, adresy, daty i numery z opisu są obowiązkowe, a niejednoznaczny kandydat wymaga prawdziwego cytatu potwierdzonego przez lokalny `qwen2.5:3b`;
- embedding porównuje opis ze wszystkimi przygotowanymi mailami, po czym adaptacyjnie kontroluje 30–80 najlepszych kandydatów oraz wszystkie wiadomości pasujące do podanych konkretów;
- stopniowy, wznawialny indeks wiadomości z możliwością anulowania i bez ponownego przeliczania niezmienionych maili;
- wyniki z nadawcą, tematem, datą, rzeczywistym pasującym cytatem i przyciskiem otwarcia oryginalnej wiadomości;
- do 5 różnych, wiarygodnych wiadomości może zostać pokazanych jednocześnie; każda musi osobno spełnić rygorystyczne warunki zapytania;
- brak wystarczającego dopasowania daje pustą listę zamiast przypadkowych propozycji; tylko kopie tej samej wiadomości są scalane po `Message-ID` lub skrócie treści;
- biblioteka **Moje dokumenty** z kategoriami Otrzymane, Zeskanowane, Wysłane, Do podpisania, Powiązane z zadaniami i Ulubione;
- lokalny indeks SQLite łączący dokument z właściwą wiadomością i zadaniem bez tworzenia dodatkowych kopii otrzymanych plików;
- podgląd PDF, wyszukiwanie po nazwie, osobie, dacie, wiadomości, zadaniu i rozpoznanej treści dokumentu;
- stronicowaną bibliotekę dokumentów z wyszukiwaniem nazw PDF w całej skrzynce bez pobierania samych plików;
- przestronny ekran PDF z przyciskami Wróć, Drukuj, Zapisz kopię i Otwórz wiadomość;
- lokalne odczytywanie warstwy tekstowej PDF oraz opcjonalny OCR Tesseract dla skanów bez tekstu;
- proste lokalne wyszukiwanie dokumentów po nazwie, nadawcy, dacie, wiadomości, zadaniu oraz odczytanej treści;
- logowanie do WP Poczty, lista wiadomości, podgląd treści i zapis załączników PDF;
- lokalna analiza Ollama z osobnymi terminami zadań i wydarzeń, warunkami, zależnościami, cytatami źródłowymi i kontrolą kompletności;
- czytelne zadania, odhaczanie postępu i prowadzenie krok po kroku;
- wykrywanie drukarek przez Qt i usługę drukowania Windows oraz skanerów udostępnionych przez WIA;
- rozróżnianie fizycznych i wirtualnych drukarek, status, typ połączenia, możliwości oraz wybór urządzeń domyślnych;
- ostrożne łączenie drukarki i skanera w urządzenie wielofunkcyjne; przy braku wspólnego identyfikatora użytkownik może połączyć funkcje ręcznie;
- podgląd PDF przed drukowaniem, liczba kopii, zakres stron, kolor, A4/Letter, orientacja i druk dwustronny;
- osobna strona testowa drukarki i wyraźne potwierdzenie fizycznego wydruku;
- skanowanie WIA ze szyby lub ADF, wiele stron, podgląd, zmiana kolejności, usuwanie stron i zapis do jednego PDF-u;
- wczytanie PDF-u zeskanowanego w programie producenta, gdy WIA nie jest dostępne;
- przyciski drukowania i skanowania przy właściwych zadaniach; polecenie „nie drukować” blokuje sugestię wydruku;
- lokalna baza instrukcji z oznaczeniem źródła, wersji i daty oraz jawnie opisana instrukcja ogólna dla nieznanego modelu;
- przewijanie, duży tekst, zwiększony kontrast i układ dopasowujący się do węższego okna.
- przygotowanie odpowiedzi z poprawnym `Reply-To`, tematem `Re:`, `In-Reply-To` i `References`;
- ręczna edycja odbiorcy, tematu i treści oraz wyraźne ostrzeżenie po zmianie adresata;
- lokalna pomoc Ollamy przy pisaniu odpowiedzi, z mechanicznym odrzucaniem niepotwierdzonych działań i dat;
- przypisywanie zatwierdzonych skanów do właściwej wiadomości, świadome dołączanie PDF-u i jego podgląd;
- automatyczne i ręczne zapisywanie szkiców zaszyfrowanych mechanizmem Windows DPAPI;
- ręczne i automatyczne (raz dziennie) szyfrowane kopie zapasowe bazy, postępu, szkiców, ustawień oraz lokalnych dokumentów;
- kontrola integralności każdej kopii, ustawiany katalog i liczba zachowywanych kopii;
- bezpieczne przywracanie z dodatkową kopią aktualnego stanu, automatycznym wycofaniem po błędzie i odzyskiem po przerwaniu operacji;
- osobna kontrola treści, odbiorców, załączników i ostrzeżeń przed wysyłką;
- wysyłanie przez bezpieczne SMTP WP dopiero po naciśnięciu **Zatwierdzam i wysyłam**;
- zapis kopii w folderze Wysłane przez IMAP, kontrola `Message-ID` i blokada przypadkowego duplikatu.

## Ważne ograniczenia

- Aplikacja **nigdy nie wysyła wiadomości automatycznie**. Ollama nie może uruchomić wysyłki. Każda wiadomość wymaga przejścia kontroli i osobnego kliknięcia **Zatwierdzam i wysyłam**.
- Wydruk jest uznawany tylko za przekazany do kolejki. Zadanie zostaje odhaczone dopiero po potwierdzeniu przez użytkownika, że kartki rzeczywiście wyszły prawidłowo.
- Skanowanie sprzętowe wymaga pełnego sterownika producenta udostępniającego urządzenie w Windows WIA. Sam sterownik drukarki nie wystarcza.
- Bez sterownika WIA można użyć programu producenta i przycisku **Wczytaj gotowy skan**.
- Sterownik może podać niepełny lub chwilowo nieaktualny stan urządzenia; aplikacja tego nie ukrywa.
- Integracja TWAIN nie jest aktywowana bez zgodnego dostawcy TWAIN dla używanej wersji Pythona. Na Windows 11 podstawowym zaimplementowanym adapterem jest WIA.
- Aplikacja nie dołącza skanu ani nie steruje urządzeniem bez wyraźnego działania użytkownika.
- Automatyczne testy używają atrap SMTP. Pierwszą rzeczywistą wysyłkę trzeba sprawdzić ręcznie na własnym koncie i bezpiecznym adresie testowym.
- Otrzymany PDF jest pobierany dopiero wtedy, gdy użytkownik chce go otworzyć, zapisać, wydrukować albo uruchomić OCR. Sama biblioteka przechowuje jego metadane i powiązanie z wiadomością.
- Przycisk **Dodaj treść dokumentu do wyszukiwania** najpierw odczytuje zwykłą warstwę tekstową PDF i nie wymaga dodatkowych programów. Tylko prawdziwy skan bez tekstu wymaga lokalnego programu Tesseract oraz języka `pol`; bez nich podgląd PDF i pozostałe funkcje nadal działają.
- Aplikacja nie przenosi ani nie usuwa wiadomości z serwera. Operacje na poczcie w Test 08A pozostają tylko do odczytu.
- Kopie są szyfrowane przez Windows DPAPI i może je odtworzyć wyłącznie to samo konto Windows. Przed zmianą komputera lub konta Windows trzeba zachować działający profil użytkownika; aplikacja nie przechowuje osobnego hasła ani klucza odzyskiwania.
- Bez działającej Ollamy lub modelu `embeddinggemma:300m` wyszukiwanie po opisie jawnie przechodzi na mniej elastyczne dopasowanie tekstowe. Zwykłe wyszukiwanie poczty pozostaje dostępne.

## Prywatność i dane

- treść wiadomości jest analizowana lokalnie pod `127.0.0.1`;
- IMAP jest otwierany w trybie tylko do odczytu;
- `QSettings` zapisuje adres konta, wygląd, urządzenia domyślne, ręczne powiązania urządzeń i postęp zadań — bez hasła i bez treści wiadomości;
- tymczasowe kopie załączników są usuwane przy rozłączeniu, a pozostałości z przerwanej sesji przy następnym uruchomieniu;
- diagnostyka zapisuje rodzaj operacji i liczbę korekt, ale nie treść maila ani dokumentu.
- hasło do poczty pozostaje wyłącznie w pamięci procesu; nie trafia do `QSettings`, szkicu ani diagnostyki;
- szkice odpowiedzi są szyfrowane przez Windows DPAPI dla aktualnie zalogowanego użytkownika;
- pliki kopii zapasowych są w całości szyfrowane przez Windows DPAPI, nie zawierają hasła do poczty ani tokenów;
- treść wiadomości i propozycja odpowiedzi trafiają wyłącznie do lokalnej Ollamy pod `127.0.0.1`.
- indeks dokumentów znajduje się w prywatnym katalogu danych aplikacji bieżącego użytkownika. Nie zawiera hasła ani binarnych kopii otrzymanych PDF-ów; zawiera metadane oraz tekst potrzebny do lokalnego wyszukiwania.
- prywatny indeks znaczeniowy `semantic_mail_index.sqlite3` zawiera fragmenty tekstu i wektory wiadomości, ale nigdy hasło. Można go bezpiecznie odbudować z poczty przyciskiem w aplikacji; dlatego nie jest wymagany do odtworzenia konta z kopii zapasowej.
- OCR działa lokalnie. Strony są renderowane wyłącznie do katalogu tymczasowego, a pliki robocze są usuwane po zakończeniu.

## Wysyłanie z WP Poczty

Aplikacja używa oficjalnych parametrów WP: `smtp.wp.pl`, port `465`, SSL/TLS i uwierzytelnianie. IMAP działa przez `imap.wp.pl`, port `993`, SSL. Dostęp programów pocztowych musi być włączony na koncie; przy weryfikacji dwuetapowej może być potrzebne osobne hasło do programu.

- [Konfiguracja programu pocztowego — pomoc WP](https://pomoc.wp.pl/jak-skonfigurowac-program-pocztowy)
- [Aktywacja dostępu dla programów pocztowych — pomoc WP](https://pomoc.wp.pl/aktywacja-dostepu-dla-programow-pocztowych)

## Testy

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Testy obejmują również foldery IMAP, stronicowanie i 10 000 wiadomości, wyszukiwanie starych maili i treści, semantyczne scenariusze projektu, dokumentu, auta, spotkania, płatności i literówek, długie maile, niepełny indeks, Recall@5 i MRR, bibliotekę 10 000 dokumentów, powiązania, brak duplikatów, tekst PDF, opcjonalny OCR, nagłówki odpowiedzi, polskie znaki, HTML, załączniki, 20-stronicowy skan, chronione szkice, niedostępną Ollamę, błędy oraz niepewny status SMTP, zabezpieczenie przed duplikatem i pełny przebieg dokumentowej odpowiedzi z atrapą serwera. Kopie są sprawdzane przez rzeczywiste odtworzenie danych, także z prawdziwym Windows DPAPI. Testy nie wysyłają prawdziwych wiadomości ani nie uruchamiają drukarki lub skanera.

## Systemy

Głównym i sprzętowo wspieranym systemem jest Windows 11. Warstwa interfejsu Qt działa również w Windows 10 (od 1809) oraz macOS, ale skanowanie WIA jest funkcją wyłącznie Windows. Windows 7 nie jest wspierany.
