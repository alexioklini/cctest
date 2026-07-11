# SQL-Abhängigkeitsreport
**Projekt:** `/Users/alexander/Documents/dev/cctest/eval/sql`  
**Stichtag:** 2026-07-11  
**Scope:** rekursiv alle `.sql`- und `.dbq`-Dateien.
## Executive Summary
- **1.321 SQL-Dateien/Units**, **231 Procedure-Definitionen**, **226 eindeutige Procedure-Namen**.
- **605 EXEC/CALL-Treffer**; daraus **21 interne Kandidatenkanten**.
- Der Code-Index wurde zuerst genutzt, ist aber nicht auf dieses SQL-Verzeichnis gescoped: `code_search` lieferte vorwiegend Analyzer-Code. `code_snippet`/`code_trace` bestätigten den Analyzer in `engine/tools/sql_analysis.py` (u. a. `_scan` Zeilen 115–141, `sql_file_symbols` Zeilen 235–328).
- Ein exaktes Duplikatpaar sowie zahlreiche wahrscheinliche Versions-/Testduplikate wurden gefunden.
## Methodik und Evidenz
1. Code-Index: `code_search`, `code_query`, `code_snippet`, `code_trace`.
2. Direkte rekursive Textanalyse: Definitionen (`CREATE/ALTER PROCEDURE`), Aufrufe (`EXEC/EXECUTE/CALL`), Tabellenreferenzen (`FROM/JOIN/UPDATE/INTO`) und SHA-256-Fingerprints.
3. Kommentare, SQL-Agent-Jobstrings, dynamische EXECs, Synonyme und echte Datenbankbindung wurden nicht vollständig semantisch aufgelöst; Ergebnisse sind daher Kandidaten, keine Laufzeitbeweise.
## Inventar
| Kennzahl | Ergebnis |
|---|---:|
| SQL-/DBQ-Dateien | 1.321 |
| Procedure-Definitionen | 231 |
| eindeutige Procedure-Namen | 226 |
| EXEC/CALL-Treffer | 605 |
| interne Kandidatenkanten | 21 |
| nicht auflösbare Callee-Namen | 8 |
| exakte Duplikatgruppen | 1 |
### Vollständiges Procedure-Inventar
| Procedure | Schema | Datei | Zeile |
|---|---|---|---:|
| `Kundenprofil_Depotbestand` | `dbo` | `q1/Queries/Kundenprofil_Depotberstand_Kurz.sql` | 18 |
| `sp_Abgelaufene_US_Dokumente` | `dbo` | `q1/Queries/Procedures/sp_Abgelaufene_US_Dokumente.sql` | 19 |
| `sp_Abgelaufene_Vollmachten` | `dbo` | `q1/Queries/Procedures/sp_Abgelaufene_Vollmachten.sql` | 22 |
| `sp_Ablaufende_Anleihen` | `dbo` | `q1/Queries/Procedures/sp_Ablaufende_Anleihen.sql` | 19 |
| `sp_Ablaufende_Festgelder` | `dbo` | `q1/Queries/Procedures/sp_Ablaufende_Festgelder.sql` | 19 |
| `sp_Aktive_Sperren_KD_KK` | `dbo` | `q1/Queries/Procedures/sp_Aktive_Sperren_KD_KK.sql` | 20 |
| `sp_AML_Meldung` | `dbo` | `q1/Queries/Procedures/sp_AML_Meldung.sql` | 19 |
| `sp_ATI_Investments` | `dbo` | `q1/Queries/Procedures/sp_ATI_Investments.sql` | 20 |
| `sp_ATI_Korrektur` | `dbo` | `q1/Queries/Procedures/sp_ATI_Korrektur.sql` | 19 |
| `sp_Bankbuch_Depotbestand` | `dbo` | `q1/Queries/Procedures/sp_Bankbuch_Depotbestand.sql` | 19 |
| `sp_Bar_Transaktionen` | `dbo` | `q1/Queries/Procedures/sp_Bar_Transaktionen.sql` | 19 |
| `sp_BEPRO_Kondition` | `dbo` | `q1/Queries/Procedures/sp_BEPRO_Kondition.sql` | 8 |
| `sp_BO_Aenderungen` | `dbo` | `q1/Queries/Procedures/sp_BO_Aenderungen.sql` | 20 |
| `sp_Bodensatz` | `dbo` | `q1/Queries/Procedures/sp_Bodensatz.sql` | 19 |
| `sp_Bodensatz_konten` | `dbo` | `q1/Queries/Procedures/sp_Bodensatz_konten.sql` | 20 |
| `sp_Buchung_GuV_Konto` | `dbo` | `q1/Queries/Procedures/sp_Buchung_GuV_Konto.sql` | 19 |
| `sp_Check24_Antrag_Inaktivieren` | `dbo` | `q1/Queries/Procedures/sp_Check24_Antrag_Inaktivieren.sql` | 20 |
| `sp_Check24_Antrag_Inaktivieren_Test` | `dbo` | `q1/Queries/Procedures/sp_Check24_Antrag_Inaktivieren_Test.sql` | 20 |
| `sp_Check_603_vs_601` | `dbo` | `q1/Queries/Procedures/sp_Check_603_vs_601.sql` | 19 |
| `sp_Check_Benchmark_VV_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Check_Benchmark_VV_Kunden.sql` | 19 |
| `sp_Check_CRS_Kontoregister` | `dbo` | `q1/Queries/Procedures/sp_Check_CRS_Kontoregister.sql` | 19 |
| `SP_Check_Depot_Spesen_Konto` | `dbo` | `q1/Queries/Procedures/SP_Check_Depot_Spesen_Konto.sql` | 20 |
| `sp_check_Depots_Bestand` | `dbo` | `q1/Queries/Procedures/sp_Check_Depots_Bestand.sql` | 20 |
| `sp_Check_Doppelte_Kest_Tilgung` | `dbo` | `q1/Queries/Procedures/sp_Check_Doppelte_Kest_Tilgung.sql` | 18 |
| `sp_Check_Eigenbestand_Lagerstelle` | `dbo` | `q1/Queries/Procedures/sp_Check_Eigenbestand_Lagerstelle.sql` | 19 |
| `sp_Check_Formular_Frequenz` | `dbo` | `q1/Queries/Procedures/sp_Check_Formular_Frequenz.sql` | 32 |
| `sp_Check_Formular_Frequenz` | `dbo` | `q1/Queries/Procedures/sp_Check_Formular_Frequenz_WORK.sql` | 26 |
| `sp_Check_Jobs` | `dbo` | `q1/Queries/Procedures/sp_Check_Jobs.sql` | 19 |
| `sp_Check_KAMA_Lieferungen` | `dbo` | `q1/Queries/Procedures/sp_Check_KAMA_Lieferungen.sql` | 25 |
| `sp_Check_Konten_ohne` | `dbo` | `q1/Queries/Procedures/sp_Check_Konten_ohne.sql` | 21 |
| `sp_Check_Konten_Zinsgruppe` | `dbo` | `q1/Queries/Procedures/sp_Check_Konten_Zinsgruppe.sql` | 20 |
| `sp_Check_Kreditkonten_neu` | `dbo` | `q1/Queries/Procedures/sp_Check_Kreditkonten_neu.sql` | 19 |
| `sp_Check_Kunden_Eroeffnungsdatum` | `dbo` | `q1/Queries/Procedures/sp_Check_Kunden_Eroeffnungsdatum.sql` | 20 |
| `sp_Check_Kunden_mit` | `dbo` | `q1/Queries/Procedures/sp_Check_Kunden_mit.sql` | 20 |
| `sp_Check_Kunden_ohne` | `dbo` | `q1/Queries/Procedures/sp_Check_Kunden_ohne.sql` | 20 |
| `sp_Check_Kunden_Team_vs_CRM` | `dbo` | `q1/Queries/Procedures/sp_Check_Kunden_Team_vs_CRM.sql` | 20 |
| `sp_Check_KundenProfil` | `dbo` | `q1/Queries/Procedures/sp_Check_KundenProfil.sql` | 19 |
| `sp_Check_Kupon_Kest` | `dbo` | `q1/Queries/Procedures/sp_Check_Kupon_Kest.sql` | 18 |
| `sp_Check_Kupon_Kest_Onbase` | `dbo` | `q1/Queries/Procedures/sp_Check_Kupon_Kest_Onbase.sql` | 20 |
| `sp_Check_LEI_Gueltigkeit` | `dbo` | `q1/Queries/Procedures/sp_Check_LEI_Gueltigkeit.sql` | 19 |
| `sp_Check_Mehrfache_Tin` | `dbo` | `q1/Queries/Procedures/sp_Check_Mehrfache_Tin.sql` | 19 |
| `sp_Check_MIFIR_Transaktionen` | `dbo` | `q1/Queries/Procedures/sp_Check_MIFIR_Transaktionen.sql` | 21 |
| `sp_Check_Portfolio_Reports` | `dbo` | `q1/Queries/Procedures/sp_Check_Portfolio_Reports.sql` | 19 |
| `sp_Check_PTP_W10` | `dbo` | `q1/Queries/Procedures/sp_Check_PTP_W10.sql` | 20 |
| `sp_Check_Quartalsspesen` | `dbo` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql` | 19 |
| `sp_Check_Relevante_Person` | `dbo` | `q1/Queries/Procedures/sp_Check_Relevante_Person.sql` | 20 |
| `sp_Check_REPP_vs_REKS` | `dbo` | `q1/Queries/Procedures/sp_Check_REPP_vs_REKS.sql` | 20 |
| `sp_Check_Risk_Scoring` | `dbo` | `q1/Queries/Procedures/sp_Check_Risk_Scoring.sql` | 22 |
| `sp_Check_SFTR_Valuation` | `dbo` | `q1/Queries/Procedures/sp_Check_SFTR_Valuation.sql` | 20 |
| `sp_Check_Smart_Invest` | `dbo` | `q1/Queries/Procedures/sp_Check_Smart_Invest.sql` | 19 |
| `SP_Check_Spesen_Konto` | `dbo` | `q1/Queries/Procedures/SP_Check_Spesen_Konto.sql` | 20 |
| `sp_Check_TIN_Gueltigkeit` | `dbo` | `q1/Queries/Procedures/sp_Check_TIN_Gueltigkeit.sql` | 19 |
| `sp_Check_Vermittlerdaten_Controlling` | `dbo` | `q1/Queries/Procedures/sp_Check_Vermittlerdaten_Controlling.sql` | 22 |
| `sp_Check_VV_Tipas` | `dbo` | `q1/Queries/Procedures/sp_Check_VV_Tipas.sql` | 20 |
| `sp_Check_WP_Art_vs_Depot` | `dbo` | `q1/Queries/Procedures/sp_Check_WP_Art_vs_Depot.sql` | 19 |
| `sp_Closed_clients_LMonth` | `dbo` | `q1/Queries/Procedures/sp_Closed_Clients_LMonth.sql` | 19 |
| `sp_Create_Ablaufende_Garantien` | `dbo` | `q1/Queries/Procedures/sp_Create_Ablaufende_Garantien.sql` | 19 |
| `sp_Create_AML_Art5` | `dbo` | `q1/Queries/Procedures/sp_Create_AML_Art5.sql` | 20 |
| `sp_Create_ATIExport_UniCredit` | `dbo` | `q1/Queries/Procedures/SP_Create_ATIExport_UniCredit.sql` | 19 |
| `sp_Create_Benutzergruppen_Menuepunkte` | `dbo` | `q1/Queries/Procedures/sp_Create_Benutzergruppen_Menuepunkte.sql` | 18 |
| `sp_Create_Best_Execution` | `dbo` | `q1/Queries/Procedures/sp_Create_Best_Execution.sql` | 19 |
| `sp_Create_Check24_Inaktiv_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Check24_Inaktiv_OnBase.sql` | 20 |
| `sp_Create_Check24_Inaktiv_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Check24_Inaktiv_OnBase_test.sql` | 20 |
| `sp_Create_Check24_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Check24_OnBase.sql` | 20 |
| `sp_Create_Check24_OnBase_Test` | `dbo` | `q1/Queries/Procedures/sp_Create_Check24_OnBase_Test.sql` | 20 |
| `sp_Create_CRS_Listen` | `dbo` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql` | 20 |
| `sp_Create_CRS_Meldung_TPAM` | `dbo` | `q1/Queries/Procedures/sp_Create_CRS_Meldung_TPAM.sql` | 19 |
| `sp_Create_CRS_Review` | `dbo` | `q1/Queries/Procedures/SP_Create_CRS_Review.sql` | 19 |
| `sp_Create_DatenExport_UniCredit` | `dbo` | `q1/Queries/Procedures/SP_Create_DatenExport_UniCredit.sql` | 19 |
| `sp_Create_ENR_Balances` | `dbo` | `q1/Queries/Procedures/sp_Create_ENR_Balances.sql` | 18 |
| `sp_Create_ENR_Positions` | `dbo` | `q1/Queries/Procedures/sp_Create_ENR_Positions.sql` | 20 |
| `sp_Create_Evidenzen_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Evidenzen_OnBase.sql` | 20 |
| `sp_Create_Evidenzen_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Evidenzen_OnBase_alt.sql` | 20 |
| `sp_Create_FinMgr_Bewegungen` | `dbo` | `q1/Queries/Procedures/sp_Create_FinMgr_Bewegungen.sql` | 19 |
| `sp_Create_FinMgr_MasterDaten` | `dbo` | `q1/Queries/Procedures/sp_Create_FinMgr_MasterDaten.sql` | 19 |
| `sp_Create_FMG_Plus_Positions` | `dbo` | `q1/Queries/Procedures/sp_Create_FMG_Plus_Positions.sql` | 19 |
| `sp_Create_goAML_Transactions` | `dbo` | `q1/Queries/Procedures/sp_Create_goAML_Transactions.sql` | 20 |
| `sp_Create_High_Volume_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Create_High_Volume_Kunden.sql` | 18 |
| `sp_Create_High_Volume_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Create_High_Volume_Kunden_alt.sql` | 18 |
| `sp_Create_High_Watermarks` | `dbo` | `q1/Queries/Procedures/sp_Create_High_Watermarks.sql` | 21 |
| `sp_Create_High_Watermarks_YtD` | `dbo` | `q1/Queries/Procedures/sp_Create_High_Watermarks_YtD.sql` | 19 |
| `sp_Create_Impairment_Test` | `dbo` | `q1/Queries/Procedures/sp_Create_Impairment_Test.sql` | 19 |
| `sp_Create_IOMA_Portfolio` | `dbo` | `q1/Queries/Procedures/sp_Create_IOMA_Portfolio.sql` | 19 |
| `sp_Create_Kest_Befreiung` | `dbo` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung.sql` | 19 |
| `sp_Create_Kest_Befreiung_Test` | `dbo` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung_Test.sql` | 19 |
| `sp_Create_Konto_saldo` | `dbo` | `q1/Queries/Procedures/sp_Create_Konto_saldo.sql` | 21 |
| `sp_Create_KPMG_Datenabzug` | `dbo` | `q1/Queries/Procedures/sp_Create_KPMG_Datenabzug.sql` | 19 |
| `sp_Create_Kredit_Evidenzen_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Kredit_Evidenzen_OnBase.sql` | 27 |
| `sp_Create_Kupon_Tilgung` | `dbo` | `q1/Queries/Procedures/sp_Create_Kupon_Tilgung.sql` | 20 |
| `sp_Create_Manual_Risk_Review_Test` | `dbo` | `q1/Queries/Procedures/sp_Create_Manual_Risk_Review_Test.sql` | 21 |
| `sp_Create_Onbase_Master_Data` | `dbo` | `q1/Queries/Procedures/sp_Create_Onbase_Master_Data.sql` | 20 |
| `sp_Create_Portfolio_Valuation` | `dbo` | `q1/Queries/Procedures/sp_Create_Portfolio_Valuation.sql` | 20 |
| `sp_Create_QI_UM_Daten` | `dbo` | `q1/Queries/Procedures/sp_Create_QI_UM_Daten.sql` | 19 |
| `sp_Create_Raquest_Analyse` | `dbo` | `q1/Queries/Procedures/sp_Create_Raquest_Analyse.sql` | 19 |
| `sp_Create_Risk_Review_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql` | 20 |
| `sp_Create_Risk_Review_OnBase_Test` | `dbo` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql` | 25 |
| `sp_Create_SRD_2_Interface` | `dbo` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql` | 19 |
| `sp_Create_SRD_2_WP_Trans` | `dbo` | `q1/Queries/Procedures/sp_Create_SRD_2_WP_Trans.sql` | 19 |
| `sp_Create_SupportNet_offen` | `dbo` | `q1/Queries/Procedures/sp_Create_SupportNet_offen.sql` | 19 |
| `sp_Create_Swiss_Alpine_Balances` | `dbo` | `q1/Queries/Procedures/sp_Create_Swiss_Alpine_Balances.sql` | 19 |
| `sp_Create_Table_LaenderStamm` | `dbo` | `q1/Queries/Procedures/sp_Create_Table_LaenderStamm.sql` | 19 |
| `sp_Create_Tambas_Assetera_Mapping` | `dbo` | `q1/Queries/Procedures/sp_Create_Tambas_Assetera_Mapping.sql` | 18 |
| `sp_Create_TCM_Check_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_TCM_Check_OnBase.sql` | 20 |
| `sp_Create_Treasury_Listen` | `dbo` | `q1/Queries/Procedures/sp_Create_Treasury_Listen.sql` | 19 |
| `sp_Create_Valorlife_Portfolio` | `dbo` | `q1/Queries/Procedures/sp_Create_Valorlife_Portfolio.sql` | 20 |
| `sp_Create_Verlustschwellenreport_Meldung` | `dbo` | `q1/Queries/Procedures/sp_Create_Verlustschwellenreport_Meldung.sql` | 20 |
| `sp_Create_WHVP_Balances` | `dbo` | `q1/Queries/Procedures/sp_Create_WHVP_Balances.sql` | 20 |
| `sp_Create_WHVP_Trades` | `dbo` | `q1/Queries/Procedures/sp_Create_WHVP_Trades.sql` | 8 |
| `sp_Create_WP_Trans_Historie` | `dbo` | `q1/Queries/Procedures/sp_Create_WP_Trans_Historie.sql` | 19 |
| `sp_Create_WPB_TCM_Clients` | `dbo` | `q1/Queries/Procedures/SP_Create_WPB_TCM_Clients.sql` | 20 |
| `sp_Create_ZVK_Eingang_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql` | 20 |
| `sp_Create_ZVK_Master_Data` | `dbo` | `q1/Queries/Procedures/sp_Create_ZVK_Master_Data.sql` | 20 |
| `sp_Create_ZVK_Valuta_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Create_ZVK_Valuta_OnBase.sql` | 18 |
| `sp_CRS_FATCA_Listen` | `dbo` | `q1/Queries/Procedures/sp_CRS_FATCA_Listen.sql` | 19 |
| `sp_Dauerauftraege_Privat` | `dbo` | `q1/Queries/Procedures/sp_Dauerauftraege_Privat.sql` | 21 |
| `sp_Devisenhandel_Vontobel` | `dbo` | `q1/Queries/Procedures/sp_Devisenhandel_Vontobel.sql` | 21 |
| `sp_Dokumente` | `dbo` | `q1/Queries/Procedures/sp_Dokumente.sql` | 20 |
| `sp_email_kundenvolumen_tipas_mailbox` | `dbo` | `q1/Queries/Procedures/sp_email_kundenvolumen_tipas_mailbox.sql` | 30 |
| `sp_email_neu_angelegte_anleihen` | `dbo` | `q1/Queries/Procedures/sp_email_neu_angelegte_anleihen.sql` | 25 |
| `sp_email_wertpapiere_umbenennen_eng` | `dbo` | `q1/Queries/Procedures/sp_email_wertpapiere_umbenennen_eng.sql` | 27 |
| `SP_ErinnerungsMail_Nachbuchen_erlaubt` | `dbo` | `q1/Queries/Procedures/SP_ErinnerungsMail_Nachbuchen_erlaubt.sql` | 21 |
| `sp_ESG_Check` | `dbo` | `q1/Queries/Procedures/sp_ESG_Check.sql` | 19 |
| `sp_Evidenzen_US_Dokumente` | `dbo` | `q1/Queries/Procedures/sp_Evidenzen_US_Dokumente.sql` | 19 |
| `sp_EvidenzVerwaltung` | `dbo` | `q1/Queries/Procedures/sp_EvidenzVerwaltung.sql` | 20 |
| `sp_FATCA_IA_Faellig` | `dbo` | `q1/Queries/Procedures/sp_FATCA_IA_Faellig.sql` | 19 |
| `sp_Fatca_Relevanz` | `dbo` | `q1/Queries/Procedures/sp_Fatca_Relevanz.sql` | 21 |
| `sp_Fehlerhafte_Corporate_Actions` | `dbo` | `q1/Queries/Procedures/sp_Fehlerhafte_Corporate_Actions.sql` | 20 |
| `sp_Fehlerhafte_Quartalsspesen` | `dbo` | `q1/Queries/Procedures/sp_Fehlerhafte_Quartalsspesen.sql` | 19 |
| `sp_findtext` | `dbo` | `q1/Queries/Procedures/sp_findtext.sql` | 25 |
| `sp_findtext_SP` | `dbo` | `q1/Queries/Procedures/sp_findtext_SP.sql` | 25 |
| `sp_Firmen_Ablaufende_Vollmachten` | `dbo` | `q1/Queries/Procedures/sp_Firmen_Ablaufende_Vollmachten.sql` | 36 |
| `sp_Firmen_Fehlende_Vollmachten` | `dbo` | `q1/Queries/Procedures/sp_Firmen_Fehlende_Vollmachten.sql` | 35 |
| `sp_Firmen_ohne_BO` | `dbo` | `q1/Queries/Procedures/sp_Firmen_ohne_BO.sql` | 19 |
| `sp_Firmen_Vollmachten` | `dbo` | `q1/Queries/Procedures/sp_Firmen_Vollmachten.sql` | 19 |
| `sp_Formulare_Inaktivieren` | `dbo` | `q1/Queries/Procedures/sp_Formulare_Inaktivieren.sql` | 19 |
| `sp_Forwards_Mature` | `dbo` | `q1/Queries/Procedures/sp_Forwards_Mature.sql` | 19 |
| `sp_FX_Forwards` | `dbo` | `q1/Queries/Procedures/sp_FX_Forwards.sql` | 21 |
| `sp_FX_Kurse_His` | `dbo` | `q1/Queries/Procedures/sp_FX_Kurse_His.sql` | 20 |
| `sp_FX_Kurse_Taeglich` | `dbo` | `q1/Queries/Procedures/sp_FX_Kurse_Taeglich.sql` | 22 |
| `sp_Geburtstagskinder` | `dbo` | `q1/Queries/Procedures/sp_Geburtstagskinder.sql` | 20 |
| `sp_Geldhandel_Check24_OnBase` | `dbo` | `q1/Queries/Procedures/sp_Geldhandel_Check24_OnBase.sql` | 20 |
| `sp_Geldhandel_Check24_OnBase_Test` | `dbo` | `q1/Queries/Procedures/sp_Geldhandel_Check24_OnBase_Test.sql` | 20 |
| `sp_Gold_Kontrakte` | `dbo` | `q1/Queries/Procedures/sp_Gold_Kontrakte.sql` | 20 |
| `sp_Gold_Sparplaene` | `dbo` | `q1/Queries/Procedures/sp_Gold_Sparplaene.sql` | 20 |
| `sp_GW_Auswertungen` | `dbo` | `q1/Queries/Procedures/sp_GW_Auswertungen.sql` | 20 |
| `sp_InvestorProfile` | `dbo` | `q1/Queries/Procedures/sp_InvestorProfile.sql` | 20 |
| `sp_Konten_Gueltigkeit` | `dbo` | `q1/Queries/Procedures/sp_Konten_Gueltigkeit.sql` | 20 |
| `sp_Konto_Abgleich_Valantic` | `dbo` | `q1/Queries/Procedures/sp_Konto_Abgleich_Valantic.sql` | 18 |
| `sp_Kontoregister_Kontrolle` | `dbo` | `q1/Queries/Procedures/sp_Kontoregister_Kontrolle.sql` | 19 |
| `sp_Kredit_Unterschreitungen` | `dbo` | `q1/Queries/Procedures/sp_Kredit_Unterschreitungen.sql` | 20 |
| `sp_Kreditkarten_Monatlich` | `dbo` | `q1/Queries/Procedures/sp_Kreditkarten_Monatlich.sql` | 20 |
| `sp_Kunden_Cash_Only` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Cash_Only.sql` | 19 |
| `sp_Kunden_Check_Compliance` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Check_Compliance.sql` | 20 |
| `sp_Kunden_Fluktuation` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Fluktuation.sql` | 22 |
| `sp_Kunden_Fluktuation_AdHoc` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Fluktuation_AdHoc.sql` | 19 |
| `sp_Kunden_Hochrisiko` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Hochrisiko.sql` | 19 |
| `sp_Kunden_Loeschung_DSGVO` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Loeschung_DSGVO.sql` | 22 |
| `sp_kunden_ohne_volumen` | `dbo` | `q1/Queries/Procedures/sp_kunden_ohne_volumen.sql` | 30 |
| `sp_Kunden_Risikoaenderung` | `dbo` | `q1/Queries/Procedures/sp_Kunden_Risikoaenderung.sql` | 19 |
| `sp_Kundenprofil_Depotbestand` | `dbo` | `q1/Queries/Procedures/sp_Kundenprofil_Depotbestand.sql` | 18 |
| `sp_Kundensperren_Compliance` | `dbo` | `q1/Queries/Procedures/sp_Kundensperren_Compliance.sql` | 19 |
| `sp_Kupon_QI_Abstimmung` | `dbo` | `q1/Queries/Procedures/sp_Kupon_QI_Abstimmung.sql` | 20 |
| `sp_Kurscheck_Nostro_Bestand` | `dbo` | `q1/Queries/Procedures/sp_Kurscheck_Nostro_Bestand.sql` | 20 |
| `sp_mail_test` | `dbo` | `q1/Queries/Procedures/sp_mail_test.sql` | 19 |
| `sp_Mailbox_vs_Spesen` | `dbo` | `q1/Queries/Procedures/sp_Mailbox_vs_Spesen.sql` | 20 |
| `sp_Mailing_Gruppen_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Mailing_Gruppen_Kunden.sql` | 20 |
| `sp_Mailing_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Mailing_Kunden.sql` | 20 |
| `sp_Manuelle_WP_Kurse` | `dbo` | `q1/Queries/Procedures/sp_Manuelle_WP_Kurse.sql` | 20 |
| `sp_MIFID_Finanzinstrumente` | `dbo` | `q1/Queries/Procedures/sp_MIFID_Finanzinstrumente.sql` | 19 |
| `sp_MIFID_II_BestEx_Offenlegung` | `dbo` | `q1/Queries/Procedures/SP_Mifid_II_BestEx_Offenlegung.sql` | 19 |
| `sp_MIFIR_Transaktionen_Onbase` | `dbo` | `q1/Queries/Procedures/sp_MIFIR_Transaktionen_Onbase.sql` | 20 |
| `sp_Neue_Wertpapiere` | `dbo` | `q1/Queries/Procedures/sp_Neue_Wertpapiere.sql` | 23 |
| `sp_Neue_WPs_Ohne_ISIN` | `dbo` | `q1/Queries/Procedures/sp_Neue_WPs_Ohne_ISIN.sql` | 20 |
| `sp_NeuKunden_Sutor` | `dbo` | `q1/Queries/Procedures/sp_NeuKunden_Sutor.sql` | 19 |
| `sp_OENB_MELDUNG_RU_BY` | `dbo` | `q1/Queries/Procedures/sp_OENB_MELDUNG_RU_BY.sql` | 19 |
| `sp_Offene_Orders` | `dbo` | `q1/Queries/Procedures/sp_Offene_Orders.sql` | 19 |
| `sp_Options` | `dbo` | `q1/Queries/Procedures/sp_Options.sql` | 19 |
| `sp_Orders_via_Navigator` | `dbo` | `q1/Queries/Procedures/sp_Orders_via_Navigator.sql` | 19 |
| `sp_OTC_Dokumente` | `dbo` | `q1/Queries/Procedures/sp_OTC_Dokumente.sql` | 19 |
| `sp_Professionelle_Kunden` | `dbo` | `q1/Queries/Procedures/sp_Professionelle_Kunden.sql` | 19 |
| `sp_Quest_Auswertung` | `dbo` | `q1/Queries/Procedures/sp_Quest_Auswertung.sql` | 20 |
| `sp_Read_Impairment_Daten` | `dbo` | `q1/Queries/Procedures/sp_Read_Impairment_Daten.sql` | 20 |
| `sp_Read_Tambas_Daten_FinMgr` | `dbo` | `q1/Queries/Procedures/sp_Read_Tambas_Daten_FinMgr.sql` | 19 |
| `sp_Realisierte_Konten` | `dbo` | `q1/Queries/Procedures/sp_Realisierte_Konten.sql` | 19 |
| `sp_Review_Nostro_Bestsand_Risk` | `dbo` | `q1/Queries/Procedures/sp_Review_Nostro_Bestsand_Risk.sql` | 20 |
| `sp_Risikoklasse_Durchschnitt_VV` | `dbo` | `q1/Queries/Procedures/sp_Risikoklasse_Durchschnitt_VV.sql` | 20 |
| `sp_Risk_OENB` | `dbo` | `q1/Queries/Procedures/sp_Risk_OENB.sql` | 20 |
| `sp_Risk_Review_Abgeschlossen` | `dbo` | `q1/Queries/Procedures/sp_Risk_Review_Abgeschlossen.sql` | 19 |
| `sp_Risk_Review_Check` | `dbo` | `q1/Queries/Procedures/sp_Risk_Review_Check.sql` | 19 |
| `sp_Risk_Review_Faellig` | `dbo` | `q1/Queries/Proceduressp_Risk_Review_Faellig.sql` | 8 |
| `sp_Risk_Review_Offen` | `dbo` | `q1/Queries/Procedures/sp_Risk_Review_Offen.sql` | 20 |
| `sp_Risk_Review_OnBase_Details` | `dbo` | `q1/Queries/Procedures/sp_Risk_Review_OnBase_Details.sql` | 20 |
| `sp_Risk_Review_OnBase_Transaktionen` | `dbo` | `q1/Queries/Procedures/sp_Risk_Review_OnBase_Transaktionen.sql` | 19 |
| `sp_RiskScoring_Kontrolle` | `dbo` | `q1/Queries/Procedures/sp_RiskScoring_Kontrolle.sql` | 19 |
| `sp_RiskScoring_Onbase` | `dbo` | `q1/Queries/Procedures/sp_RiskScoring_Onbase.sql` | 19 |
| `sp_Salden_KO_Sperre_CS` | `dbo` | `q1/Queries/Procedures/sp_Salden_KO_Sperre_CS.sql` | 20 |
| `sp_Send_SRD_2_CSV` | `dbo` | `q1/Queries/Procedures/sp_Send_SRD_2_CSV.sql` | 19 |
| `sp_sperraenderung` | `dbo` | `q1/Queries/Procedures/sp_Sperraenderungen.sql` | 20 |
| `sp_sperrquittierungen` | `dbo` | `q1/Queries/Procedures/sp_sperrquittierungen.sql` | 20 |
| `sp_sperrquittierungen_Quartal` | `dbo` | `q1/Queries/Procedures/sp_sperrquittierungen_Quartal.sql` | 19 |
| `sp_SupportNet_vs_YouTrack` | `dbo` | `q1/Queries/Procedures/sp_SupportNet_vs_YouTrack.sql` | 19 |
| `sp_test` | `dbo` | `q1/Queries/Procedures/a_sp_Test.sql` | 4 |
| `sp_Ueberziehungen` | `dbo` | `q1/Queries/Procedures/sp_Ueberziehungen.sql` | 20 |
| `sp_Ueberziehungen_Test` | `dbo` | `q1/Queries/Procedures/sp_Ueberziehungen_TEST.sql` | 20 |
| `sp_Vollmacht_Sperren` | `dbo` | `q1/Queries/Procedures/sp_Vollmacht_Sperren.sql` | 20 |
| `sp_Vollmachten_PEP` | `dbo` | `q1/Queries/Procedures/sp_Vollmachten_PEP.sql` | 20 |
| `sp_VST_9999800011_Gegenbuchung` | `dbo` | `q1/Queries/Procedures/sp_VST_9999800011_Gegenbuchung.sql` | 19 |
| `sp_VV_Depot_Check` | `dbo` | `q1/Queries/Procedures/sp_VV_Depot_Check.sql` | 21 |
| `sp_VV_Depot_Check` | `dbo` | `q1/Queries/VV_Depot_Check.sql` | 8 |
| `sp_VV_IP_` | `dbo` | `q1/Queries/VV_IP_ÄnderungAnlage.sql` | 9 |
| `sp_VV_IP_AenderungAnlage` | `dbo` | `q1/Queries/Procedures/sp_VV_IP_AenderungAnlage.sql` | 20 |
| `sp_WP_Bewegungen` | `dbo` | `q1/Queries/Procedures/sp_WP_Bewegungen.sql` | 19 |
| `sp_WP_Kontrakte_Taeglich` | `dbo` | `q1/Queries/Procedures/sp_WP_Kontrakte_Taeglich.sql` | 19 |
| `sp_WP_Orders` | `dbo` | `q1/Queries/Procedures/sp_WP_Orders.sql` | 19 |
| `sp_WP_Trans_Check` | `dbo` | `q1/Queries/Procedures/sp_WP_Trans_Check.sql` | 20 |
| `sp_Write_Kunden_MonatsendDaten` | `dbo` | `q1/Queries/Procedures/sp_Write_Kunden_MonatsendDaten.sql` | 21 |
| `sp_Write_Kunden_Postfach` | `dbo` | `q1/Queries/Procedures/sp_Write_Kunden_Postfach.sql` | 20 |
| `sp_Write_Kunden_Salden` | `dbo` | `q1/Queries/Procedures/sp_Write_Kunden_Salden.sql` | 20 |
| `sp_Write_Kunden_Sprache` | `dbo` | `q1/Queries/Procedures/sp_Write_Kunden_Sprache.sql` | 21 |
| `sp_Write_Kundenstamm` | `dbo` | `q1/Queries/Procedures/sp_Write_Kundenstamm_Controlling.sql` | 26 |
| `sp_Write_Treasury_Salden` | `dbo` | `q1/Queries/Procedures/sp_Write_Treasury_Salden.sql` | 20 |
| `sp_ZVK_Ausgaenge_OnBase` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Ausgaenge_OnBase.sql` | 18 |
| `sp_ZVK_Compliance` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Compliance.sql` | 19 |
| `sp_ZVK_Eingang_Check24` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Eingang_Check24.sql` | 19 |
| `sp_ZVK_Eingang_Check24_Test` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Eingang_Check24_Test.sql` | 19 |
| `sp_ZVK_Kontrakte` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Kontrakte.sql` | 19 |
| `sp_ZVK_Kontrakte_Ford_Verb` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Kontrakte_Ford_Verb.sql` | 24 |
| `sp_ZVK_RU_BY_UA` | `dbo` | `q1/Queries/Procedures/sp_ZVK_RU_BY_UA.sql` | 21 |
| `sp_ZVK_Sepa_Ausgaenge_OnBase` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Sepa_Ausgaenge_OnBase.sql` | 20 |
| `sp_ZVK_Taeglich` | `dbo` | `q1/Queries/Procedures/sp_ZVK_Taeglich.sql` | 19 |
| `sp_ZVK_VS_Schwellenwert` | `dbo` | `q1/Queries/Procedures/sp_ZVK_VS_Schwellenwert.sql` | 20 |
## Abhängigkeitsmatrix / Graphbeschreibung
Kanten: `Aufrufer → Callee`; bei mehreren Definitionskandidaten je Datei ist die Zuordnung unsicher.

| Aufrufer | Callee | Datei:Zeile | Evidenz |
|---|---|---|---|
| `sp_ATI_Korrektur` | `sp_ATI_Investments` | `q1/Queries/Procedures/sp_ATI_Korrektur.sql:59` | statischer Name-Match |
| `sp_Check_Quartalsspesen` | `SP_Check_Depot_Spesen_Konto` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql:72` | statischer Name-Match |
| `sp_Check_Quartalsspesen` | `SP_Check_Spesen_Konto` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql:68` | statischer Name-Match |
| `sp_Create_CRS_Listen` | `sp_Check_Mehrfache_Tin` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql:245` | statischer Name-Match |
| `sp_Create_CRS_Listen` | `sp_Create_CRS_Review` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql:34` | statischer Name-Match |
| `sp_Create_FinMgr_MasterDaten` | `sp_Read_Tambas_Daten_FinMgr` | `q1/Queries/Procedures/sp_Create_FinMgr_MasterDaten.sql:80` | statischer Name-Match |
| `sp_Create_Impairment_Test` | `sp_Read_Impairment_Daten` | `q1/Queries/Procedures/sp_Create_Impairment_Test.sql:316` | statischer Name-Match |
| `sp_Create_Kest_Befreiung_Test` | `sp_Create_Kest_Befreiung` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung_Test.sql:739` | statischer Name-Match |
| `sp_Create_Risk_Review_OnBase` | `sp_Risk_Review_OnBase_Transaktionen` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql:144` | statischer Name-Match |
| `sp_Create_Risk_Review_OnBase` | `sp_RiskScoring_Onbase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql:142` | statischer Name-Match |
| `sp_Create_Risk_Review_OnBase_Test` | `sp_Risk_Review_OnBase_Transaktionen` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql:380` | statischer Name-Match |
| `sp_Create_Risk_Review_OnBase_Test` | `sp_RiskScoring_Onbase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql:378` | statischer Name-Match |
| `sp_Create_SRD_2_Interface` | `sp_Create_SRD_2_WP_Trans` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql:459` | statischer Name-Match |
| `sp_Create_SRD_2_Interface` | `sp_Send_SRD_2_CSV` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql:989` | statischer Name-Match |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_Create_ZVK_Master_Data` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql:66` | statischer Name-Match |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_Create_ZVK_Valuta_OnBase` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql:67` | statischer Name-Match |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_ZVK_VS_Schwellenwert` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql:68` | statischer Name-Match |
| `sp_SupportNet_vs_YouTrack` | `sp_Create_SupportNet_offen` | `q1/Queries/Procedures/sp_SupportNet_vs_YouTrack.sql:40` | statischer Name-Match |
| `sp_Ueberziehungen_Test` | `sp_Ueberziehungen` | `q1/Queries/Procedures/sp_Ueberziehungen_TEST.sql:658` | statischer Name-Match |
| `sp_Ueberziehungen_Test` | `sp_Ueberziehungen` | `q1/Queries/Procedures/sp_Ueberziehungen_TEST.sql:663` | statischer Name-Match |
| `sp_ZVK_Taeglich` | `sp_ZVK_RU_BY_UA` | `q1/Queries/Procedures/sp_ZVK_Taeglich.sql:752` | statischer Name-Match |

### Scheduler und externe Knoten
- `q1/Queries/Jobs.sql` ist ein großer SQL-Agent-Hub mit `@command=N'exec ...'`; Beispiele: `sp_VV_Depot_Check` ca. Zeile 1251, `sp_Write_Kunden_Salden` ca. 1522, `sp_Create_CRS_Listen` ca. 2562, `sp_Check_SFTR_Valuation` ca. 2991, `sp_Create_Portfolio_Valuation` ca. 5061.
- System-/Fremdabhängigkeiten: `msdb.dbo.sp_send_dbmail` (z. B. `sp_Check_Benchmark_VV_Kunden.sql:92`, `sp_ESG_Check.sql:231`), `internal.cleanup_server_retention_window`, `internal.cleanup_server_project_version` (Jobs.sql ca. 4016–4031) und `msdb.dbo.sp_syspolicy_purge_history` (ca. 4114).
- Viele Query-Dateien ohne Definition sind isolierte Blätter; Procedures, die nur von Jobs/manuell gestartet werden, haben keinen lokalen Procedure-Aufrufer.
- Selbstaufruf-ähnliche Run-Snippets existieren z. B. in `sp_Risk_Review_Faellig`, `sp_Check_Kunden_Eroeffnungsdatum` und `sp_ESG_Check`; Kommentar-/Teststatus muss validiert werden.
### Häufige Tabellen-/Objekt-Referenzen
| Objekt | Dateien/Referenzen |
|---|---:|
| `IWPBPRD.H000DTA` | 620 |
| `IWPBPRD.H010DTA` | 454 |
| `IWPBPRD` | 423 |
| `H000DTA.PS00` | 339 |
| `Sysobjects` | 320 |
| `OPENQUERY` | 313 |
| `H000DTA.IE05` | 304 |
| `H000DTA.KD00` | 267 |
| `H000DTA.CF` | 234 |
| `H000DTA.SB00` | 213 |
| `IWPBPRD.THOBJ` | 209 |
| `inbound.dbo` | 159 |
| `H010DTA.KK00` | 149 |
| `H010DTA.DP00` | 143 |
| `H000DTA` | 137 |
| `Kalender` | 132 |
| `H000DTA.AD00` | 123 |
| `H000DTA.KN00` | 115 |
| `H000DTA.WS00` | 102 |
| `THOBJ.FX00` | 88 |
| `H000DTA.PF00` | 81 |
| `H010DTA` | 77 |
| `H010DTA.VS00` | 74 |
| `THOBJ.PA` | 71 |
| `H010DTA.KB` | 62 |
| `H000DTA.PFEV00` | 55 |
| `H000DTA.RV00` | 55 |
| `H000DTA.WSSU` | 54 |
| `IWPBPRD.TOBJ` | 54 |
| `THOBJ.LA00` | 51 |
| `TOBJ` | 49 |
| `H010DTA.DA` | 42 |
| `H000DTATST.CF` | 40 |
| `IWPBPRD.H000DTATST` | 40 |
| `H000DTA.RISC00` | 39 |
| `IWPBPRD.H010SAV` | 35 |
| `H000DTA.PP00` | 31 |
| `H000DTA.IE00` | 30 |
| `H010DTA.DB` | 30 |
| `inbound` | 28 |
| `H000DTATST.KD00` | 28 |
| `H010DTA.ZVKK00` | 26 |
| `H000DTA.KV` | 26 |
| `H000DTATST.IE05` | 26 |
| `H010DTATST.KK00` | 25 |
| `H000DTATST` | 25 |
| `H010DTA.WF` | 24 |
| `H010DTA.BT` | 24 |
| `Kunde` | 20 |
| `H010DTA.KKSA` | 20 |
## Versions-, Duplikat- und Qualitätsprobleme
### Exakte Duplikate
- Hash `910cb202564ea7a4`:
  - `./q2/Abfragen Neu/Firmen_VollmachtenCIS_CEE.dbq`
  - `./q2/Abfragen Neu/Kunden_VollmachtenCIS_CEE.dbq`
- Hohe Versionsdrift durch `old/`, `Test/`, `CHECK_unfertig/`, `0 old/` sowie `_TEST`, `_V1`, `_v2`, `_2024`, `_2025`, `_2026_unfertig`.
- Konkrete Warnsignale: `sp_Create_Kest_Befreiung_Test.sql` ruft im Run-Snippet `sp_Create_Kest_Befreiung`; `VV_IP_ÄnderungAnlage.sql` weist eine Encoding-Auffälligkeit im Procedure-Namen auf; `Proceduressp_Risk_Review_Faellig.sql` verletzt die erwartete Dateinamenskonvention.
## Risiken
| Risiko | Bewertung | Begründung |
|---|---|---|
| Dynamische EXEC-Auflösung | hoch | String-EXECs und Laufzeitobjekte fehlen im statischen Graphen. |
| Falsche Produktivfassung | hoch | old/Test/Jahresvarianten ohne erkennbaren Single Source of Truth. |
| Scheduler außerhalb lokaler Definitionen | hoch | Jobs.sql referenziert viele nicht mitgelieferte Routinen. |
| Mail/System/Linked-Server-Integration | mittel | msdb, dbmail, interne Cleanup-Routinen und Fremdsysteme. |
| Encoding-/Namensdrift | mittel | erschwert Matching, Deployment und Betrieb. |
| Kommentar-/Testaufrufe | mittel | können Rekursion bzw. Aufrufer vortäuschen. |
## Empfehlungen
1. Produktivbestand und kanonischen Pfad je Procedure festlegen; `old`, `Test`, `CHECK_unfertig` aus Deployments ausschließen.
2. Zielinstanzgraph aus `sys.sql_modules`, `sys.objects`, `sys.sql_expression_dependencies`, `sys.dm_sql_referencing_entities`, `sys.dm_sql_referenced_entities`, `msdb.dbo.sysjobs` und `sysjobsteps` ergänzen.
3. Dynamisches SQL und `sp_executesql` separat erfassen oder per Query Store/Telemetry beobachten.
4. Schemaqualifizierung, Unicode/UTF-8 und Namenskonventionen erzwingen.
5. Normalisierte SQL-/AST-Fingerprints und CI-Gate gegen Near-Duplicates einsetzen.
6. Revisionsfähigen Graphen mit `caller`, `callee`, `source`, `line`, `confidence`, `scheduler`, `external_system` erzeugen; SCC-/Zyklenerkennung auf validierten Kanten ausführen.
7. Externe Abhängigkeiten mit Owner, Berechtigungen, Ausfallverhalten und Mailprofil katalogisieren.
## Unsicherheiten
- Texttreffer bestätigen nicht die Kompilierbarkeit auf der Ziel-SQL-Server-Version.
- Kommentare/Jobstring-/Run-Blöcke verfälschen Kandidatenzahlen.
- CTEs, temporäre Tabellen, Synonyme, dynamische Objekte und Cross-Database-Referenzen können über-/untererfasst sein.
- Für einen revisionssicheren Abschluss ist ein Abgleich gegen Zielinstanz und Deploymentbestand erforderlich.
