# 📱 TREND·CREW auf Replit – Schritt für Schritt (für absolute Anfänger)

> **Stand: November 2026.** Replit ändert seine Oberfläche gelegentlich. Wenn ein Knopf etwas anders heißt, such den
> ähnlichsten – die Reihenfolge der Schritte bleibt gleich. Die Replit-Menünamen stammen aus Replits Dokumentation; die Menünamen der
> Börsen (Kraken/Binance) kenne ich nur nach bestem Wissen – entscheidend ist dort immer: **Auszahlen/Withdraw bleibt AUS.**

---

## 0. Bevor du anfängst – bitte kurz lesen (2 Minuten)

| | |
|---|---|
| **Was du bekommst** | Einen Bot, der **BTC, ETH und SOL** nach festen mathematischen Regeln (Trend, RSI, MACD, Risiko-Rechnung) handelt – ohne KI-Kosten – und dir alles auf einem Handy-Dashboard zeigt. |
| **Startmodus** | **DEMO** mit 10.000 € **Spielgeld**. Es wird *nichts* echt gekauft. Du brauchst dafür weder Börsenkonto noch API-Schlüssel. |
| **Ehrliche Warnung** | Es gibt **keine Gewinn-Garantie**. Im Testlauf über 5,8 Jahre brachte die Strategie ein *kleines* Plus bei kleinem Verlustrisiko – das kann in Zukunft anders aussehen. Setze nur Geld ein, dessen Verlust du verschmerzen kannst. Keine Finanzberatung. |
| **Es passiert selten etwas** | Der Bot kauft nur bei bestätigtem Trend-Ausbruch – im Schnitt **ca. 2-mal pro Monat**. Wochenlang „nichts“ ist normal und gewollt. Im Dashboard siehst du trotzdem live, was die Crew prüft. |
| **Kosten** | Replit-Konto (zum Testen kostenlos). Für den **24/7-Betrieb** brauchst du bei Replit ein **„Reserved VM“-Deployment**. Laut Replit-Preisliste (Nov 2026) kostet die kleinste Stufe ca. **15 US-$ pro Monat**, verrechnet mit den Credits deines Plans. Prüfe den aktuellen Preis direkt in Replit. Der Bot selbst hat **keine** laufenden Kosten. |
| **Dauer** | Demo einrichten: ca. 20–30 Minuten. |

**Die goldene Regel:** Lass den Bot mindestens **4 Wochen im Demo-Modus** laufen, bevor du über echtes Geld nachdenkst.

---

## 1. Den Code bekommen

Du brauchst die Datei **`trend-crew.zip`**. Du hast sie vom Assistenten erhalten. Alternativ:

1. Öffne <https://github.com/walteropa0-bot/aitradingcrew/blob/claude/zealous-hamilton-5da0rd/trend-crew/dist/trend-crew.zip>
2. Klicke rechts oben auf das **Download-Symbol** („Download raw file“).

Die Datei landet in deinem Download-Ordner. **Nicht entpacken** – Replit nimmt die ZIP direkt.

---

## 2. Bei Replit anmelden und das Projekt importieren

1. Gehe auf <https://replit.com> und erstelle ein kostenloses Konto (oder melde dich an).
2. Öffne <https://replit.com/import>.
3. Wähle **ZIP**.
4. Lade **`trend-crew.zip`** hoch.
5. Wenn Replit fragt „*Add any requested secrets and environment variables*“: **überspringen** (machen wir gleich in Schritt 3).
6. Klicke **Import**.

Replit richtet jetzt alles ein (installiert Pakete, erkennt den Startbefehl). Das dauert 1–3 Minuten.
*(Falls der „Agent“ Änderungen vorschlägt: **nichts ändern lassen** – die Dateien sind fertig.)*

✅ **Du siehst jetzt:** links eine Dateiliste mit `main.py`, `config.py`, `README.md`, `crew/` …

---

## 3. Geheimnisse („Secrets“) eintragen

Passwörter und Schlüssel gehören **nie** in eine Datei, sondern in Replits Tresor („Secrets“ = Umgebungsvariablen). Der Bot liest sie von dort.

**So öffnest du den Tresor:** Oben auf **Tools** klicken → im Bereich **Setup** auf **Secrets**.
**So legst du ein Secret an:** **New Secret** → **Key** (Name) und **Value** (Wert) eintragen → **Add Secret**.
*(Schneller: im Secrets-Fenster Drei-Punkte-Menü → **Edit as .env** → mehrere Zeilen `NAME=WERT` einfügen.)*

### Für den Demo-Start brauchst du genau EIN Secret:

| Key | Value | Wofür |
|---|---|---|
| `DASHBOARD_PASSWORD` | ein langes, einzigartiges Passwort (z. B. 4 zufällige Wörter + Zahl) | Schützt dein Dashboard. Ohne Passwort könnte jeder, der den Link kennt, deinen Bot sehen und bedienen. |

Das war's für die Demo. Die Börsen-Schlüssel brauchst du erst für Live (Schritt 9).

---

## 4. Zum ersten Mal starten

1. Klicke oben auf den grünen **Run**-Knopf.
2. Unten im Fenster **Console** siehst du Text wie:
   `=== TREND·CREW v1.0.0 · DEMO-MODUS (Spielgeld) · Börse kraken · Port 5000 ===`
3. Rechts erscheint die **Preview** (Vorschau) mit deinem Dashboard → Passwort eingeben → du bist drin.
4. Tippe auf **[BOT STARTEN]**. Oben erscheint **LÄUFT**.

*Falls nichts passiert / Fehler „ModuleNotFoundError“:* unten auf den Reiter **Shell** wechseln, `pip install -r requirements.txt` eingeben, Enter, danach wieder **Run**.
*Falls Run gar nichts startet:* **Tools → Workflows** → neuer Workflow → Aufgabe **Execute Shell Command** → Befehl `python main.py` → speichern und ausführen.

---

## 5. Verbindungstest (wichtig!)

Prüfe, ob Replit die Börse erreicht. Unten auf den Reiter **Shell** wechseln und eingeben:

```
python selftest.py
```

Du solltest lauter ✅ sehen (Börse erreichbar, BTC/EUR, ETH/EUR, SOL/EUR mit Kursen und Kerzen) und am Ende **„Alles bereit ✓“**.

* ❌ **„… 451 …“ / „restricted location“** → Die Börse sperrt den Standort des Servers (typisch bei **Binance** auf US-Servern). Lass `EXCHANGE` auf dem Standard **`kraken`** (funktioniert von Replit aus).
* ❌ **„Markt … gibt es nicht“** → `QUOTE_CURRENCY` passt nicht zur Börse (Kraken: `EUR`, `USD`, `USDT` …).

---

## 6. Rund um die Uhr laufen lassen („Veröffentlichen“)

Die Vorschau im Editor ist nur zum Testen (privat, vorübergehend). Für den Dauerbetrieb **veröffentlichst** du den Bot:

1. Oben rechts auf **Publish** klicken.
2. **Adjust settings** (Einstellungen anpassen) öffnen.
3. **Deployment type** → **Reserved VM** wählen. ⚠️ *Nicht* „Autoscale“ – das schläft ein und würde den Bot ausschalten.
4. **Machine power**: die kleinste Stufe reicht.
5. Bei **Who can access your app** kannst du **Public** lassen (dein Dashboard hat ja ein eigenes Passwort).
6. Auf **Publish** klicken und warten, bis „Erfolgreich“ erscheint.

✅ Replit zeigt dir jetzt die **öffentliche Adresse** deiner App, z. B. `https://dein-name.replit.app`. **Das ist der Link für dein Handy.**
*Deine Secrets werden beim Veröffentlichen automatisch übernommen.*

### Damit Verlauf & offene Positionen Neustarts überleben (empfohlen)

Replit warnt: Dateien, die eine veröffentlichte App zur Laufzeit schreibt, sind **nicht dauerhaft**. Der Bot nutzt deshalb automatisch die **Replit-Datenbank**, sobald es eine gibt:

1. **Vor** dem ersten Veröffentlichen: **Tools → Database** (PostgreSQL) anlegen. Replit erzeugt dabei automatisch das Secret `DATABASE_URL`.
2. Den Bot einmal mit **Run** starten und ~30 Sekunden laufen lassen (er legt seine Tabelle in der Entwicklungs-Datenbank an).
3. Beim ersten **Publish** die Datenbank-Einstellung auf der Standard-Option lassen.
4. Kontrolle im Dashboard ganz unten: **System → Speicher** muss **„Postgres-Datenbank“** zeigen. Steht dort „Lokale Datei“, ist die Datenbank nicht verbunden – dann geht der Verlauf bei einer Neu-Veröffentlichung verloren (im Demo-Modus verschmerzbar; **im Live-Modus unbedingt beheben**).

---

## 7. Auf dem iPhone (Safari)

1. Öffne in **Safari** deine Adresse `https://dein-name.replit.app` und melde dich an (Safari merkt sich die Anmeldung einige Tage – Safari begrenzt das selbst auf ca. 7 Tage; beim „Home-Bildschirm“-Symbol musst du dich einmal neu anmelden).
2. Tippe auf das **Teilen**-Symbol (Quadrat mit Pfeil) → **„Zum Home-Bildschirm“** → **Hinzufügen**.
3. Jetzt hast du ein App-Symbol **TREND·CREW** auf dem Handy – ohne Adressleiste, wie eine echte App.

---

## 8. So liest du das Dashboard (von oben nach unten)

| Bereich | Bedeutung |
|---|---|
| **DEMO / LIVE · LÄUFT / PAUSE / GESTOPPT** (ganz oben) | Modus und Zustand. **LIVE** leuchtet rot – dann geht es um echtes Geld. |
| **Gesamtwert** | Dein Konto (Cash + offene Positionen). Grün = im Plus seit Start, Rot = im Minus. „Heute“ ist die Veränderung seit 00:00 UTC. |
| **[BOT STARTEN] / [BOT PAUSIEREN]** | Startet bzw. pausiert **neue Käufe**. Offene Positionen bleiben beim Pausieren durch ihre Stopps geschützt. |
| **[NOT-AUS: ALLES LIQUIDIEREN]** | Rot pulsierend. Nach einer Sicherheitsabfrage verkauft er **sofort alles** zum Marktpreis und stoppt den Bot. Danach: [BOT STARTEN], um weiterzumachen. |
| **Performance** | Interaktiver Chart (Finger drüberziehen zeigt Werte): **grün** über dem Startwert, **rot** darunter. Oben wählst du 1 Std / 24 Std / 7 Tage / Alles. |
| **Die Crew bei der Arbeit** | Drei Karten mit pulsierendem Punkt: 🔵 *arbeitet gerade* (z. B. „Scout liest Live-Kurse …“, „Tech-Analyst berechnet RSI & MACD …“, „Risikomanager prüft Trend & Tageslimit …“) · 🟢 *fertig/ok* · 🟠 *Warnung* · 🔴 *Fehler*. Darunter die **Live-Aktivität** als Protokoll. |
| **Märkte** | Pro Coin: Kurs, 24h-Änderung, **Trend** (aus Tageskerzen), **RSI-Messer** (blau = überverkauft ≤ 30, rot = überkauft ≥ 70), MACD-Richtung und die **Kauf-Checkliste** (✓/✗). **KAUFSIGNAL** leuchtet nur, wenn *alle* Haken sitzen. Dazu: „nächste Kerze in …“. |
| **Offene Positionen** | Gewinn/Verlust, **Stop** (die Verkaufsgrenze, die nur nach oben wandert) und Abstand dazu, geplantes Risiko. |
| **Transaktionen** | Die letzten Käufe/Verkäufe, animiert. Neue erscheinen mit einem kurzen Aufleuchten. |
| **Kennzahlen / System** | Gewinn-Quote, Profit-Faktor, Gebühren, Rückgang vom Höchststand; Börsen-Verbindung, Speicherart, Strategie, Risiko-Einstellungen. |

**Wichtig zum Verständnis:** Der Bot kauft *nur*, wenn (1) der Tages-Trend aufwärts zeigt, (2) der Kurs auf ein neues 50-Kerzen-Hoch (≈ 8 Tage) ausbricht, (3) der MACD positiv ist und (4) der RSI zwischen 50 und 80 liegt. Das passiert selten – dafür sind die Verluste klein.

---

## 9. Auf ECHTES GELD umstellen (nur wenn du es wirklich willst)

> ⚠️ **Mach das erst nach mindestens 4 Wochen Demo.** Ich (der Assistent) konnte **keine echten Orders mit echtem Geld** ausprobieren – es gab keinen Börsen-Schlüssel.
> Die Live-Logik ist gegen eine Nachbildung der Kraken-Schnittstelle gründlich getestet (siehe README), aber **dein erster Live-Trade ist der erste echte Praxistest**.
> Fange mit einem Betrag an, den du verschmerzen kannst (z. B. 100 €).

### Checkliste

**a) Börsenkonto:** Lege (falls noch nicht vorhanden) ein Konto bei **Kraken** an (<https://www.kraken.com>), schließe die Verifizierung ab und zahle per SEPA Euro ein.
Der Bot handelt direkt dort. **Dein Geld bleibt die ganze Zeit auf deinem Kraken-Konto.**

**b) API-Schlüssel erstellen – mit den *richtigen* Rechten** (bei Kraken: Einstellungen → **Sicherheit → API** → Schlüssel hinzufügen):

| Recht | |
|---|---|
| ✅ Guthaben abfragen (*Query funds*) | AN |
| ✅ Offene / abgeschlossene Orders & Trades abfragen (*Query open / closed orders & trades*) | AN |
| ✅ Orders erstellen & ändern (*Create & modify orders*) | AN |
| ✅ Orders abbrechen (*Cancel & close orders*) | AN |
| ❌ **Auszahlen (*Withdraw funds*)** | **AUS – immer!** |
| ❌ Einzahlen (*Deposit funds*) und alles andere | AUS |

Kopiere **API-Key** und **Private Key** (Secret) – das Secret wird meist nur **einmal** angezeigt. Zeige beides niemandem, schicke es nie per Chat/Mail.
*(Bei Binance: nur „Enable Reading“ und „Enable Spot & Margin Trading“ – **nie** „Enable Withdrawals“. Beachte: Binance.com sperrt oft US-Server, siehe Schritt 5.)*

**c) Secrets anlegen** (Tools → Setup → Secrets):

| Key | Value |
|---|---|
| `EXCHANGE_API_KEY` | der API-Key aus b) |
| `EXCHANGE_API_SECRET` | der Private Key aus b) |
| `LIVE_CAPITAL` | der **Höchstbetrag in Euro**, den der Bot einsetzen darf, z. B. `100` |
| `DASHBOARD_PASSWORD` | (hast du schon – **im Live-Modus Pflicht**) |

Der Bot fasst **nie mehr** an als `LIVE_CAPITAL`, auch wenn mehr auf dem Konto liegt, und rührt **keine anderen Coins** auf deinem Konto an.

**d) Verbindungstest mit Schlüssel:** In der **Shell** `python selftest.py` → du solltest u. a. „API-Key gültig“ und dein freies Guthaben sehen. Meldet er „Dieser API-Key darf Geld AUSZAHLEN“, erstelle den Key neu (ohne dieses Recht).

**e) Den Schalter umlegen** – ganz oben in der Datei **`config.py`**:

```python
PAPER_TRADING = False      # False = ECHTES GELD
```

**f) Neu veröffentlichen:** **Publish → Republish**. *Vorher im Dashboard **NOT-AUS** drücken, damit keine Demo-Positionen im Konto hängen.*

**g) Nur EINE Instanz!** Im Live-Modus darf der Bot **nicht gleichzeitig im Editor (Run) und veröffentlicht** laufen – sonst handeln zwei Programme mit demselben Konto. Drücke im Editor oben **Stop** und nutze nur die veröffentlichte Adresse.

**h) Starten:** Dashboard öffnen → oben steht rot **LIVE** → **[BOT STARTEN]** → Sicherheitsabfrage bestätigen. Beim ersten Live-Start legt der Bot sein Budget fest (`LIVE_CAPITAL` oder, falls weniger frei ist, das freie Guthaben).
Beobachte die ersten Trades genau und merke dir, wo **NOT-AUS** ist.

### Wo landen die Gewinne? Wie komme ich an mein Geld?

* Der Bot kauft und verkauft **direkt auf deinem Kraken-Konto**. Gewinne und Verluste landen sofort in deinem Euro-Guthaben **dort** – dein Geld verlässt diese Börse nie.
* **Der Bot kann kein Geld an andere Adressen senden.** Im Code gibt es keine Auszahlungsfunktion; dein Schlüssel hat dieses Recht ohnehin nicht.
* **Auszahlung auf dein Bankkonto** machst *du* wie gewohnt bei Kraken: **Auszahlen → Euro (SEPA)** → dein Bankkonto. Beachte mögliche **Steuerpflichten** in deinem Land.
* **Budget ändern** (z. B. von 100 auf 300 €): Secret `LIVE_CAPITAL` ändern → **Republish** → im Dashboard **[BOT PAUSIEREN]** (und ggf. NOT-AUS, damit nichts mehr offen ist) → ganz unten **„Live-Budget neu festlegen“**.

---

## 10. Sicherheits-Regeln (kurz)

1. ✅ API-Key **ohne Auszahlungsrecht**. ❌ Nie mit Auszahlungsrecht.
2. ✅ Schlüssel **nur** als Secret. ❌ Nie in Code, Chat, Screenshot oder GitHub.
3. ✅ Langes **Dashboard-Passwort**. Ohne Passwort ist der Live-Modus komplett gesperrt.
4. ✅ **NOT-AUS** kennen. Bei Unsicherheit: erst NOT-AUS, dann nachdenken.
5. ✅ Regelmäßig bei Kraken prüfen, ob Bestand und Dashboard zusammenpassen.
6. ✅ Kleines Budget (`LIVE_CAPITAL`), das du verschmerzen kannst.
7. ❌ Niemandem „Support“-Zugriff auf Replit/Börse geben. Echte Supportleute fragen nie nach deinem Secret.
8. ✅ Wenn ein Schlüssel je offen lag: bei der Börse sofort **löschen**, neuen erzeugen, Secret austauschen.
9. ⚠️ Der Stopp wird *vom Bot* überwacht (nicht als Stopp-Order bei der Börse). Läuft der Server nicht, ist eine offene Position **ungeschützt** – deshalb „Reserved VM“ und kleines Budget.

---

## 11. Häufige Probleme

| Problem | Lösung |
|---|---|
| Seite lädt nicht / weiß | In Replit unten **Console** ansehen. Fehlen Pakete: **Shell** → `pip install -r requirements.txt` → **Run**. |
| „Falsches Passwort“ | In Secrets `DASHBOARD_PASSWORD` prüfen/ändern → **Republish**. Nach 5 Fehlversuchen sperrt das Dashboard 5 Minuten. |
| Roter Hinweis „Keine Verbindung zur Börse“ | Meist kurzer Ausfall – der Bot versucht es automatisch erneut. Bleibt es: `python selftest.py` in der Shell. Bei „451“: `EXCHANGE=kraken` verwenden. |
| Es passiert nichts / keine Trades | **Normal** (strenge Regeln, ca. 2 Trades/Monat). Schau bei **Märkte**, welche Haken fehlen. |
| „Live-Handel gesperrt: …“ | Der rote Hinweis nennt, was fehlt (Passwort, Schlüssel oder `LIVE_CAPITAL`). Secret anlegen → Republish. |
| „Dein API-Key darf Geld AUSZAHLEN“ | Neuen Key ohne Auszahlungsrecht erstellen (Schritt 9b), Secret austauschen. |
| „Zu wenig freies EUR auf der Börse“ | Mehr Euro auf das Börsenkonto einzahlen (mindestens 10 €, besser ≥ 100 €). Bei sehr kleinen Konten verlangt die Börse Mindestordergrößen – der Bot meldet „unter dem Börsen-Minimum“. |
| System → Speicher zeigt „Lokale Datei“ | Datenbank nicht verbunden (Schritt 6). Im Live-Modus vor jedem Republish alles per NOT-AUS schließen. |
| „Unklare Order wird abgeglichen“ | Die Börse hat nach einem Verbindungsfehler nicht bestätigt, ob die Order lief. Der Bot gleicht selbst mit deinem Guthaben ab (bis zu 3 Minuten) und sendet **nie** doppelt. Schau parallel in deinem Börsenkonto nach. |
| Position lässt sich nicht verkaufen | Die Börse lehnt die Order ab (z. B. zu kleine Menge). Der Bot versucht es weiter; notfalls manuell bei der Börse verkaufen. |

## 12. Aktualisieren / zurück zum Demo-Modus

* Neue Version: ZIP erneut importieren (neues Projekt) oder Dateien ersetzen, dann **Republish**.
* Zurück zu Spielgeld: `PAPER_TRADING = True` in `config.py` → vorher **NOT-AUS** → **Republish**.
* Demo-Konto zurücksetzen: Dashboard ganz unten → **„Demo-Konto zurücksetzen“**.
* Rückblick-Test mit echten Kursen: in der **Shell** `python -m backtest.run` (lädt einmalig die Kurse, ca. 2–3 Minuten).

Viel Erfolg – und bleib vorsichtig. 🚀
