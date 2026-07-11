# Zusammenfassung der Tabelle **PS00**

## **1. Allgemeine Beschreibung**
Die Tabelle **PS00** ist eine **Stammdaten-Tabelle** für Personen in der Wiener Privatbank. Sie enthält grundlegende Informationen zu Kunden, Mitarbeitern und anderen Personen, die in den Geschäftsprozessen relevant sind.

PS00 wird als **Basis für viele Abfragen** genutzt, da sie zentrale Personeninformationen wie Name, Status, und regulatorische Daten enthält.

---

## **2. Spaltenübersicht**

| **Spalte**   | **Datentyp**       | **Beschreibung**                                                                                     | **Beispiele für Verwendung**                                                                                     |
|--------------|--------------------|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| **PSIPID**   | NUMERIC            | **Primärschlüssel**: Eindeutige Personen-ID. Wird als Fremdschlüssel in vielen anderen Tabellen verwendet (z.B. IE05, KD00, RV00). | `JOIN H000DTA.PS00 PS00 ON IE05.IEIPID = PS00.PSIPID`

| **PSKURZ**   | VARCHAR(35)        | **Kurzbezeichnung** der Person (Name oder Abkürzung). Wird in den meisten Abfragen als Anzeigename genutzt. | `SELECT PS00.PSKURZ AS Name FROM H000DTA.PS00`

| **PSFNA1**   | VARCHAR            | **Nachname** der Person. Wird in Abfragen wie `OnBase_Persons.sql` referenziert.                     | `SELECT PS00.PSFNA1 AS Nachname FROM H000DTA.PS00`

| **PSPSSJ**   | NUMERIC            | **Status** der Person (z.B. `0` für aktiv, andere Werte für inaktiv oder spezielle Status). Wird in vielen Abfragen als Filterkriterium genutzt. | `WHERE PS00.PSPSSJ = 0`

| **PSIDNR**   | VARCHAR            | **OENB-Identifikationsnummer** (z.B. für regulatorische Meldungen wie OENB oder FATCA).               | `SELECT PS00.PSIDNR AS OENB_Ident FROM H000DTA.PS00`

| **PSMANR**   | NUMERIC            | **Mandantennummer** (z.B. für interne Zuordnung oder Compliance-Zwecke).                            | `SELECT PS00.PSMANR AS Mandant FROM H000DTA.PS00`

| **PSNACE**   | VARCHAR            | **NACE-Code** (Wirtschaftszweigklassifikation, z.B. für Compliance oder Statistiken).                   | `SELECT PS00.PSNACE AS NACE_Code FROM H000DTA.PS00`

| **PSFATC**   | VARCHAR            | **FATCA-Status** (z.B. ob die Person FATCA-relevant ist). Wird in FATCA-Abfragen genutzt.              | `SELECT PS00.PSFATC AS FATCA_Status FROM H000DTA.PS00`

---

## **3. Typische Verwendungszwecke**

### **3.1. Kundenstammdaten**
PS00 wird häufig als Basis für **Kundenabfragen** genutzt, z.B.:
- **Aktive Kunden identifizieren** (`aktive_Kunden.sql`)
- **Kunden mit bestimmten Risikoklassen filtern** (`Kunden_RiskCode.sql`)
- **Kunden für regulatorische Meldungen extrahieren** (`OENB_Meldung_RU_BY_2024.sql`)

### **3.2. Mitarbeiterdaten**
PS00 enthält auch **Mitarbeiterdaten**, die in Abfragen wie:
- **Betreuer und Teams zuordnen** (`ZVK_Spezialabfrage.sql`, `VV_Depot_Check.sql`)
- **Vollmachten verwalten** (`DOK247_Vollmachten.sql`)

### **3.3. Compliance und Meldewesen**
Spalten wie `PSFATC` oder `PSNACE` werden für **regulatorische Meldungen** genutzt:
- **FATCA-Meldungen** (`FATCA_adhoc.sql`)
- **OENB-Meldungen** (`OENB_Meldung_RU_BY_2024.sql`)
- **Compliance-Prüfungen** (z.B. Sanktionen, Risikoklassen)

### **3.4. OnBase-Integration**
In Abfragen wie `OnBase_Persons.sql` oder `OnBase_Individuals.sql` wird PS00 mit OnBase-Daten verknüpft, um Personeninformationen für **Dokumentenmanagement-Systeme** bereitzustellen.

---

## **4. Beispiele für SQL-Abfragen mit PS00**

### **4.1. Abfrage: Aktive Kunden (`aktive_Kunden.sql`)**
```sql
SELECT
    IEKNDN AS Kunde,
    PS00.PSKURZ AS Name,
    PS00.PSIPID AS Personen_ID
FROM
    H000DTA.KD00 KD00
JOIN
    H000DTA.PS00 PS00 ON KD00.KDIPID = PS00.PSIPID
WHERE
    PS00.PSPSSJ = 0
    AND KD00.KDKDSJ = 0;
```

### **4.2. Abfrage: FATCA-relevante Kunden (`FATCA_adhoc.sql`)**
```sql
SELECT
    IEKNDN AS Kunde,
    PS00.PSKURZ AS Name,
    PS00.PSFATC AS FATCA_Status
FROM
    H000DTA.KD00 KD00
JOIN
    H000DTA.PS00 PS00 ON KD00.KDIPID = PS00.PSIPID
WHERE
    PS00.PSFATC = 'J'
    AND PS00.PSMANR = 0;
```

### **4.3. Abfrage: OnBase-Personen (`OnBase_Persons.sql`)**
```sql
SELECT
    PP00.PPIPID AS IPID,
    IEKNDN AS Tambas_Nummer,
    PS00.PSFNA1 AS Nachname,
    PPNAME AS Vorname,
    PS00.PSKURZ AS Kurzbezeichnung
FROM
    H000DTA.PP00 PP00
JOIN
    H000DTA.PS00 PS00 ON PP00.PPIPID = PS00.PSIPID
WHERE
    PS00.PSPSSJ = 0;
```

---

## **5. Häufige Join-Partner von PS00**
PS00 wird in den meisten Abfragen mit anderen Tabellen verknüpft, um zusätzliche Informationen zu ergänzen:

| **Tabelle**       | **Join-Schlüssel**       | **Zweck**                                                                                     |
|-------------------|--------------------------|----------------------------------------------------------------------------------------------|
| **IE05**          | PS05.PSIPID = IE05.IEIPID | Verknüpft Personen mit Konten oder Depots.                                                   |
| **KD00**          | PS00.PSIPID = KD00.KDIPID | Kundenstammdaten (z.B. Kundennummer, Status).                                                |
| **RV00**          | PS00.PSIPID = RV00.RVNR   | Vollmachten und Vertretungsregelungen.                                                      |
| **SB00**          | PS00.PSIPID = SB00.SBIPID | Betreuer- und Teamzuordnung.                                                                 |
| **AD00**          | PS00.PSIPID = AD00.ADIPID | Adressdaten der Person.                                                                       |
| **PP00**          | PS00.PSIPID = PP00.PPIPID | Personendaten für natürliche Personen (z.B. Vorname, Geburtsdatum).                          |

---

## **6. Wichtige Abfragen, die PS00 nutzen**

| **Abfrage**                          | **Zweck**                                                                                     | **Referenzierte Spalten in PS00**                          |
|---------------------------------------|----------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| `ZVK_Spezialabfrage.sql`              | ZVK-Meldungen (Zahlungsverkehr) für Compliance.                                               | `PSIPID`, `PSKURZ`                                         |
| `aktive_Kunden.sql`                   | Liste der aktiven Kunden.                                                                    | `PSIPID`, `PSKURZ`, `PSPSSJ`                               |
| `Kunden_RiskCode.sql`                 | Kunden mit bestimmten Risikoklassen.                                                         | `PSIPID`, `PSKURZ`                                         |
| `OnBase_Persons.sql`                  | Personenstammdaten für OnBase (Dokumentenmanagement).                                        | `PSIPID`, `PSKURZ`, `PSFNA1`                               |
| `FATCA_adhoc.sql`                     | FATCA-relevante Personen identifizieren.                                                     | `PSIPID`, `PSKURZ`, `PSFATC`, `PSMANR`                     |
| `OENB_Meldung_RU_BY_2024.sql`         | Regulatorische Meldungen für OENB (Österreichische Nationalbank).                            | `PSIPID`, `PSKURZ`, `PSIDNR`                               |
| `DOK247_Vollmachten.sql`              | Vollmachten für Dokumente verwalten.                                                        | `PSIPID`, `PSKURZ`                                         |

---

## **7. Fazit**
- **PS00 ist eine zentrale Stammdaten-Tabelle** für Personen in der Wiener Privatbank.
- Sie enthält **Identifikationsdaten** (`PSIPID`), **Namensdaten** (`PSKURZ`, `PSFNA1`), **Statusdaten** (`PSPSSJ`), und **regulatorische Daten** (`PSFATC`, `PSNACE`).
- PS00 wird in **fast allen Abfragen** als Basis für Joins mit anderen Tabellen genutzt.
- Sie ist essenziell für **Kundenmanagement**, **Compliance**, **Meldewesen** und **Dokumentenmanagement (OnBase)**.

---

### **Anlagen**
- [SQL-Abfragen, die PS00 nutzen](#6-wichtige-abfragen-die-ps00-nutzen)
- [Typische Join-Partner von PS00](#5-häufige-join-partner-von-ps00)

---
**Erstellt am:** 2026-06-30
**Projekt:** sql und showcase
**Zweck:** Dokumentation der Tabelle PS00 für Bankweite SQL- und ShowCase-Auswertungen
