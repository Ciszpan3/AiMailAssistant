# Asystent Poczty

Zrobiłem tę aplikację z myślą o moich rodzicach.

Dość często wołali mnie, żebym pomógł im znaleźć konkretną wiadomość, pobrać dokument z poczty, sprawdzić załącznik, przygotować odpowiedź albo odesłać zeskanowany formularz. Zwykle nie chodziło o nic szczególnie trudnego — problemem było odnalezienie właściwej opcji, zrozumienie długiego maila i upewnienie się, że wszystko zostało zrobione w odpowiedniej kolejności.

Asystent Poczty powstał właśnie po to, żeby takie codzienne sytuacje były prostsze i mniej stresujące.

## O co chodzi w tej aplikacji?

To desktopowy klient poczty, który nie tylko pokazuje wiadomości, ale pomaga zrozumieć, co właściwie trzeba z nimi zrobić.

Aplikacja potrafi przełożyć długi mail na czytelne kroki, oddzielić termin wykonania zadania od daty wydarzenia, odnaleźć starszą wiadomość na podstawie zwykłego opisu i zebrać dokumenty PDF w jednym miejscu. Pomaga również przygotować odpowiedź, obsłużyć skan lub wydruk i wrócić do rozpoczętego zadania bez ponownego analizowania całej wiadomości.

Interfejs celowo ma duże, czytelne elementy i prowadzi użytkownika krok po kroku. Techniczne szczegóły są schowane, dopóki naprawdę nie są potrzebne.

Nie chciałem jednak, żeby aplikacja robiła coś za użytkownika bez jego wiedzy. Wiadomości nie są wysyłane automatycznie, a każdą odpowiedź, załącznik i operację można wcześniej sprawdzić.

## Technologie

Projekt powstał w Pythonie i korzysta głównie z:

- **PySide6 / Qt** — interfejs aplikacji, podgląd PDF i obsługa drukowania;
- **IMAP i SMTP** — pobieranie oraz ręczne wysyłanie wiadomości z WP Poczty;
- **Ollama + `qwen2.5:3b`** — lokalne analizowanie maili i pomoc w przygotowywaniu odpowiedzi;
- **`embeddinggemma:300m`** — lokalne wyszukiwanie wiadomości po ich znaczeniu, nawet bez pamiętania dokładnego tematu;
- **SQLite** — lokalny indeks wiadomości, dokumentów i powiązań;
- **Windows DPAPI** — szyfrowanie szkiców oraz kopii zapasowych;
- **Windows WIA** — współpraca ze skanerami na Windows.

Analiza AI odbywa się lokalnie przez Ollamę. Hasło do poczty pozostaje wyłącznie w pamięci uruchomionej aplikacji i nie jest zapisywane w ustawieniach ani w repozytorium.

## Uruchomienie

Projekt jest rozwijany i testowany przede wszystkim na Windows 11 z Pythonem 3.10–3.15.

W PowerShell, w katalogu projektu:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Potrzebna jest również lokalna Ollama z dwoma modelami:

```powershell
ollama pull qwen2.5:3b
ollama pull embeddinggemma:300m
```

Następnie aplikację można uruchomić poleceniem:

```powershell
python mail_assistant_qt.py
```

Jeżeli PowerShell blokuje aktywację środowiska, można uruchomić aplikację bez aktywowania `.venv`:

```powershell
.\.venv\Scripts\python.exe mail_assistant_qt.py
```

## Bezpieczeństwo

- aplikacja nie wysyła wiadomości bez wyraźnego zatwierdzenia;
- przeglądanie poczty nie usuwa ani nie przenosi wiadomości na serwerze;
- analiza wiadomości i dokumentów odbywa się lokalnie;
- otrzymane pliki PDF są pobierane dopiero wtedy, gdy użytkownik chce je otworzyć, zapisać lub wydrukować;
- dane konta, lokalne bazy, szkice, kopie zapasowe i środowisko `.venv` są wykluczone z repozytorium.

## Testy

```powershell
python -m unittest discover -v
```

Testy nie wysyłają prawdziwych wiadomości i nie uruchamiają fizycznej drukarki ani skanera.

## Kilka słów o projekcie

Pierwsza wersja aplikacji powstała w `tkinter`. Zachowałem ją w pliku `ai_assistant_04a.py` jako punkt odniesienia. Obecna wersja korzysta z PySide6 i jest uruchamiana przez `mail_assistant_qt.py`.

Projekt nadal rozwijam przede wszystkim wokół jednego celu: osoba, która nie czuje się pewnie przy komputerze, powinna móc samodzielnie przeczytać wiadomość, znaleźć dokument i bezpiecznie wykonać potrzebne czynności.
