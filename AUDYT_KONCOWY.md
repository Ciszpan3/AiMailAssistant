# Końcowy audyt Asystenta Poczty

Data audytu: 30.10.2026

## Wynik

Aplikacja uruchamiana przez `mail_assistant_qt.py` korzysta z pakietu
`mailassistant_qt/`. Plik referencyjny `ai_assistant_04a.py` nie został zmieniony.
Nowa aplikacja nie importuje tkinter.

Pełny zestaw automatyczny zakończył się wynikiem:

- **214 testów wykonanych**;
- **214 testów zaliczonych**;
- **0 błędów i 0 niezaliczonych testów**;
- wszystkie moduły kompilują się poprawnie;
- środowisko Pythona nie zgłasza uszkodzonych ani sprzecznych zależności.

## Naprawiony widok i wyszukiwanie po opisie

- karta wyniku jest ponownie mierzona po ustaleniu rzeczywistej szerokości;
- przycisk **Otwórz wiadomość** znajduje się przed długim cytatem i pozostaje
  widoczny również w niskim oknie;
- pole opisu jest niższe na małym ekranie, a lista wyników otrzymuje więcej miejsca;
- wysokości kart są aktualizowane po zmianie rozmiaru okna;
- komunikat nie twierdzi już, że przygotowywanie trwa, gdy proces jest zatrzymany;
- nazwiska, daty i adresy rozpoznane mechanicznie nie uruchamiają zbędnej,
  dodatkowej analizy Qwen;
- zapytanie „Kowalska coś ode mnie chciała” wymaga teraz zarówno nazwiska, jak
  i rzeczywistej, dodatniej prośby w treści maila;
- neutralna wzmianka o osobie oraz instrukcje typu „nie trzeba” są odrzucane;
- prawidłowy cytat wybrany przez weryfikator nie jest zastępowany innym zdaniem
  z tej samej wiadomości;
- awaryjne wyszukiwanie tekstowe zachowuje te same wymagania dotyczące osoby i prośby;
- czas oczekiwania na lokalny model jest ograniczony, a zbędne wywołanie modelu
  dla dokładnego nazwiska zostało usunięte.

## Zwarta lista poczty i zwijane menu

- każda wiadomość zajmuje jeden zwarty wiersz z nadawcą, tematem, fragmentem,
  oznaczeniami i datą;
- długie wartości są skracane tylko wizualnie, bez utraty pełnego tekstu;
- na węższym ekranie najpierw ukrywany jest fragment, dzięki czemu temat i data
  pozostają czytelne;
- lista poczty otrzymała większą część szerokości i wysokości okna, a panel
  folderów został zwężony;
- filtry i sortowanie są domyślnie schowane pod czytelnym przyciskiem;
- menu główne można zwinąć do samych ikon i ponownie rozwinąć;
- wybrany stan menu jest zapamiętywany po zamknięciu aplikacji.

## Czytelne przeglądanie całej historii poczty

- dawny przycisk doklejający wiadomości zastąpiono stronami;
- przyciski **← Nowsze** i **Starsze →** pozwalają poruszać się w obu kierunkach;
- aplikacja pokazuje dokładny zakres, łączną liczbę wiadomości oraz numer strony,
  np. `51–100 z 347 • strona 2 z 7`;
- użytkownik może wybrać 25, 50 albo 100 wiadomości na stronie;
- przejście na kolejną stronę zastępuje listę właściwą partią zamiast tworzyć
  coraz dłuższą, niejasną listę;
- stronicowanie działa również w widoku **Wszystkie wiadomości**;
- wiadomości łączone z kilku folderów są porządkowane według rzeczywistej daty,
  a nie tekstowego zapisu daty.

## Cała skrzynka i stronicowanie dokumentów

- zwykłe wyszukiwanie w Poczcie domyślnie działa w całej skrzynce na serwerze,
  a nie tylko wśród 25–100 widocznych wiadomości;
- użytkownik może nadal ograniczyć wyszukiwanie do otwartego folderu;
- zakładka Moje dokumenty otrzymała strony **← Nowsze** i **Starsze →**, dokładny
  zakres wyników oraz wybór 25, 50 lub 100 dokumentów na stronę;
- opcja **Szukaj w całej skrzynce pocztowej** przygotowuje foldery partiami po
  100 wiadomości i zapisuje lokalnie wyłącznie metadane oraz nazwy PDF;
- same pliki PDF nie są pobierane podczas przygotowywania listy;
- przyciski zmieniają opis na „Pobieranie nowszych…” albo „Pobieranie starszych…”
  i pozostają zablokowane do zakończenia operacji;
- partię metadanych zapisuje jedna transakcja SQLite, co ogranicza liczbę operacji
  dyskowych przy dużej skrzynce;
- test 10 000 wiadomości zakończył przygotowanie indeksu w około 1,7 s, a wybór
  strony 1 000 znalezionych dokumentów trwał około 0,02 s na środowisku testowym.

## Szybsze i stabilniejsze przełączanie stron

- nagłówki całej strony są pobierane z WP jednym zbiorczym żądaniem IMAP zamiast
  osobnego żądania dla każdej wiadomości;
- odwiedzone strony są przechowywane w lekkiej pamięci bieżącej sesji, dlatego
  powrót do nich nie wymaga kolejnego pobierania;
- ręczne odświeżenie czyści właściwą część pamięci, aby nowe dane nadal były widoczne;
- lista korzysta teraz z natywnej tabeli Qt i nie tworzy osobnego widżetu dla
  każdej wiadomości;
- lista jest przebudowywana bez pośredniego rysowania kolejnych wierszy;
- usunięto opóźnione odświeżenia należące do starych wierszy oraz modalne błędy
  operacji pocztowych, które mogły pojawiać się jako migające, puste okna podczas
  logowania lub przełączania strony;
- błędy logowania, odświeżania i pobierania strony są teraz wyświetlane w stałym
  komunikacie odpowiedniego ekranu, bez zasłaniania aplikacji;
- status `Nowa` i `Załącznik PDF` znajduje się w jednym wierszu obok daty i nie
  wychodzi poza granice wiadomości.

Test obciążeniowy wykonał 100 kolejnych zmian strony po 50 wiadomości w 0,53 s.
Po teście nie pozostało żadne dodatkowe ani odłączone okno wiersza wiadomości.

## Pełna obsługa polskich znaków

- treści i nagłówki wiadomości są odczytywane jako UTF-8, Windows-1250,
  ISO-8859-2 oraz w starszych zgodnych kodowaniach;
- błędna deklaracja kodowania nadawcy nie zamienia już liter na znak `�`;
- naprawiane są typowe uszkodzenia po podwójnym dekodowaniu, np. `Å›` zamiast `ś`;
- polskie nazwy załączników są odczytywane również z formatów MIME RFC 2047
  i RFC 2231;
- techniczne dane IMAP nie są już przedwcześnie dekodowane jako UTF-8;
- testy obejmują zdanie „Zażółć gęślą jaźń” i nazwę
  `zaświadczenie.pdf` w kilku rzeczywistych wariantach kodowania.

Poprawiony algorytm został sprawdzony również na kopii rzeczywistego lokalnego
indeksu. Dla badanego zapytania zwrócił właściwy mail i rzeczywisty fragment:
„Proszę potwierdzić udział w spotkaniu do 30 października 2026 r.”

## Naprawiona usterka załączników PDF

Przyczyną braku PDF-ów po kliknięciu wiadomości było współdzielenie pamięci
podręcznej z wyszukiwaniem po opisie. Indeksowanie pobierało pełną treść maila,
ale odrzucało listę załączników. Przy późniejszym otwarciu aplikacja widziała już
treść i błędnie uznawała, że cała wiadomość została pobrana.

Wprowadzono następujące poprawki:

- indeksowanie w tle zachowuje części MIME PDF i ich nazwy;
- otwarcie wiadomości sprawdza osobno kompletność treści i załączników;
- gdy treść jest w pamięci, ale lista załączników nie została pobrana, aplikacja
  automatycznie pobiera kompletne szczegóły z serwera;
- nazwy PDF znane już z listy wiadomości są pokazane podczas pobierania;
- wykrywane są również PDF-y oznaczone jako `inline` oraz PDF-y bez poprawnie
  podanej nazwy pliku;
- pobieranie i zapisywanie nadal sprawdza prawdziwy nagłówek pliku `%PDF-`, więc
  samo błędne rozszerzenie nie pozwala zapisać nieprawidłowego dokumentu.

Dodano sześć testów dokładnie dla tego przepływu: rzeczywiste kliknięcie karty,
niepełna pamięć podręczna, indeksowanie w tle, PDF osadzony, niepełna nazwa PDF
oraz przekazanie załączników do otwartej wiadomości.

## Sprawdzone obszary

- uruchamianie PySide6 i nawigacja między stronami;
- ochrona wiadomości, dokumentów, zadań i odpowiedzi przed zalogowaniem;
- logowanie WP, foldery IMAP, stronicowanie, odświeżanie i brak duplikatów;
- zwykłe wyszukiwanie poczty oraz rygorystyczne wyszukiwanie po opisie;
- osobny, przewijany widok wiadomości i responsywne karty list;
- treść MIME bez technicznych granic `Content-Type`;
- lista, pobieranie, zapis i podgląd PDF;
- biblioteka dokumentów, łączenie kopii, polskie nazwy, OCR i indeks tekstu;
- analiza Ollama, walidacja JSON, osobne terminy zadań i wydarzeń;
- odhaczanie zadań i zapis postępu;
- przygotowanie, kontrola i ręczne wysyłanie odpowiedzi;
- wykrywanie urządzeń, drukowanie i skanowanie w testach bez fizycznej ingerencji;
- szkice, kopie zapasowe, integralność danych i bezpieczne operacje na plikach;
- tryb prosty, kreator startowy, klawiatura, większy tekst i kontrast;
- zamykanie pracowników działających w tle.

## Ograniczenia wymagające rzeczywistego środowiska

Automatyczne testy nie mogą zagwarantować stanu zewnętrznych usług ani sprzętu.
W praktyce nadal są potrzebne:

- działające połączenie z WP Pocztą i poprawne dane konta;
- uruchomiona Ollama z modelami `qwen2.5:3b` i `embeddinggemma:300m`;
- sterownik WIA producenta dla fizycznego skanera;
- dostępna kolejka Windows dla fizycznej drukarki;
- ręczne potwierdzenie pierwszej testowej wysyłki i pierwszego wydruku.

Aplikacja nie wysyła wiadomości automatycznie. Każda wysyłka nadal wymaga
osobnej kontroli i wyraźnego zatwierdzenia przez użytkownika.
