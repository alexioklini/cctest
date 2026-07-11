# Abhängigkeits-Report: SQL-Prozeduren

**Projekt:** sql und showcase  
**Quelle:** `q1/Queries/Procedures/*.sql`  
**Erstellt:** 2026-07-11

## Executive Summary

- **Prozedurdateien:** 227
- **Erkannte Prozeduren:** 223
- **Direkte Prozedur-zu-Prozedur-Abhängigkeiten:** 30
- **Prozeduren mit ausgehenden Abhängigkeiten:** 22
- **Prozeduren mit eingehenden Abhängigkeiten:** 28
- **Dynamic SQL:** 166
- **DML:** 197
- **Cursor/FETCH:** 100

## Bewertungslogik

Abhängigkeiten werden statisch aus `EXEC`/`EXECUTE`-Aufrufen und Funktionsaufruf-Syntax gegen die im Verzeichnis erkannten Prozedurnamen ermittelt. Tabellen-/View-Zugriffe werden zusätzlich extrahiert. Nicht erkannte dynamische oder indirekte Aufrufe sind möglich; die Ergebnisse sind daher ein statischer Impact-Analyse-Baseline.

## Prozedur-zu-Prozedur-Abhängigkeiten

| Aufrufer | Aufgerufene Prozedur | Datei |
|---|---|---|
| `sp_ATI_Korrektur` | `sp_ATI_Investments` | `q1/Queries/Procedures/sp_ATI_Korrektur.sql` |
| `sp_Bodensatz_konten` | `sp_Bodensatz` | `q1/Queries/Procedures/sp_Bodensatz_konten.sql` |
| `sp_Check24_Antrag_Inaktivieren_Test` | `sp_Check24_Antrag_Inaktivieren` | `q1/Queries/Procedures/sp_Check24_Antrag_Inaktivieren_Test.sql` |
| `sp_Check_Quartalsspesen` | `SP_Check_Depot_Spesen_Konto` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql` |
| `sp_Check_Quartalsspesen` | `SP_Check_Spesen_Konto` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql` |
| `sp_Create_Check24_OnBase_Test` | `sp_Create_Check24_OnBase` | `q1/Queries/Procedures/sp_Create_Check24_OnBase_Test.sql` |
| `sp_Create_CRS_Listen` | `sp_Check_Mehrfache_Tin` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql` |
| `sp_Create_CRS_Listen` | `sp_Create_CRS_Review` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql` |
| `sp_Create_FinMgr_MasterDaten` | `sp_Read_Tambas_Daten_FinMgr` | `q1/Queries/Procedures/sp_Create_FinMgr_MasterDaten.sql` |
| `sp_Create_High_Watermarks_YtD` | `sp_Create_High_Watermarks` | `q1/Queries/Procedures/sp_Create_High_Watermarks_YtD.sql` |
| `sp_Create_Impairment_Test` | `sp_Read_Impairment_Daten` | `q1/Queries/Procedures/sp_Create_Impairment_Test.sql` |
| `sp_Create_Kest_Befreiung_Test` | `sp_Create_Kest_Befreiung` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung_Test.sql` |
| `sp_Create_Risk_Review_OnBase` | `sp_Risk_Review_OnBase_Transaktionen` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql` |
| `sp_Create_Risk_Review_OnBase` | `sp_RiskScoring_Onbase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql` |
| `sp_Create_Risk_Review_OnBase_Test` | `sp_Create_Risk_Review_OnBase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql` |
| `sp_Create_Risk_Review_OnBase_Test` | `sp_Risk_Review_OnBase_Transaktionen` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql` |
| `sp_Create_Risk_Review_OnBase_Test` | `sp_RiskScoring_Onbase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql` |
| `sp_Create_SRD_2_Interface` | `sp_Create_SRD_2_WP_Trans` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql` |
| `sp_Create_SRD_2_Interface` | `sp_Send_SRD_2_CSV` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql` |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_Create_ZVK_Master_Data` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql` |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_Create_ZVK_Valuta_OnBase` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql` |
| `sp_Create_ZVK_Eingang_OnBase` | `sp_ZVK_VS_Schwellenwert` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql` |
| `sp_Geldhandel_Check24_OnBase_Test` | `sp_Geldhandel_Check24_OnBase` | `q1/Queries/Procedures/sp_Geldhandel_Check24_OnBase_Test.sql` |
| `sp_Kunden_Fluktuation_AdHoc` | `sp_Kunden_Fluktuation` | `q1/Queries/Procedures/sp_Kunden_Fluktuation_AdHoc.sql` |
| `sp_sperrquittierungen_Quartal` | `sp_sperrquittierungen` | `q1/Queries/Procedures/sp_sperrquittierungen_Quartal.sql` |
| `sp_SupportNet_vs_YouTrack` | `sp_Create_SupportNet_offen` | `q1/Queries/Procedures/sp_SupportNet_vs_YouTrack.sql` |
| `sp_Ueberziehungen_Test` | `sp_Ueberziehungen` | `q1/Queries/Procedures/sp_Ueberziehungen_TEST.sql` |
| `sp_ZVK_Eingang_Check24_Test` | `sp_ZVK_Eingang_Check24` | `q1/Queries/Procedures/sp_ZVK_Eingang_Check24_Test.sql` |
| `sp_ZVK_Kontrakte_Ford_Verb` | `sp_ZVK_Kontrakte` | `q1/Queries/Procedures/sp_ZVK_Kontrakte_Ford_Verb.sql` |
| `sp_ZVK_Taeglich` | `sp_ZVK_RU_BY_UA` | `q1/Queries/Procedures/sp_ZVK_Taeglich.sql` |

## Zentralität / Impact-Kandidaten

| Prozedur | Eingehende Aufrufe | Ausgehende Aufrufe | Einordnung |
|---|---:|---:|---|
| `sp_Risk_Review_OnBase_Transaktionen` | 2 | 0 | zentraler Baustein |
| `sp_RiskScoring_Onbase` | 2 | 0 | zentraler Baustein |
| `sp_Create_Risk_Review_OnBase` | 1 | 2 | Orchestrator |
| `sp_ATI_Investments` | 1 | 0 | gekoppelt |
| `sp_Bodensatz` | 1 | 0 | gekoppelt |
| `sp_Check24_Antrag_Inaktivieren` | 1 | 0 | gekoppelt |
| `SP_Check_Depot_Spesen_Konto` | 1 | 0 | gekoppelt |
| `sp_Check_Mehrfache_Tin` | 1 | 0 | gekoppelt |
| `SP_Check_Spesen_Konto` | 1 | 0 | gekoppelt |
| `sp_Create_Check24_OnBase` | 1 | 0 | gekoppelt |
| `sp_Create_CRS_Review` | 1 | 0 | gekoppelt |
| `sp_Create_High_Watermarks` | 1 | 0 | gekoppelt |
| `sp_Create_Kest_Befreiung` | 1 | 0 | gekoppelt |
| `sp_Create_SRD_2_WP_Trans` | 1 | 0 | gekoppelt |
| `sp_Create_SupportNet_offen` | 1 | 0 | gekoppelt |
| `sp_Create_ZVK_Master_Data` | 1 | 0 | gekoppelt |
| `sp_Create_ZVK_Valuta_OnBase` | 1 | 0 | gekoppelt |
| `sp_Geldhandel_Check24_OnBase` | 1 | 0 | gekoppelt |
| `sp_Kunden_Fluktuation` | 1 | 0 | gekoppelt |
| `sp_Read_Impairment_Daten` | 1 | 0 | gekoppelt |
| `sp_Read_Tambas_Daten_FinMgr` | 1 | 0 | gekoppelt |
| `sp_Send_SRD_2_CSV` | 1 | 0 | gekoppelt |
| `sp_sperrquittierungen` | 1 | 0 | gekoppelt |
| `sp_Ueberziehungen` | 1 | 0 | gekoppelt |
| `sp_ZVK_Eingang_Check24` | 1 | 0 | gekoppelt |
| `sp_ZVK_Kontrakte` | 1 | 0 | gekoppelt |
| `sp_ZVK_RU_BY_UA` | 1 | 0 | gekoppelt |
| `sp_ZVK_VS_Schwellenwert` | 1 | 0 | gekoppelt |
| `sp_Create_Risk_Review_OnBase_Test` | 0 | 3 | Orchestrator |
| `sp_Create_ZVK_Eingang_OnBase` | 0 | 3 | Orchestrator |
| `sp_Check_Quartalsspesen` | 0 | 2 | Orchestrator |
| `sp_Create_CRS_Listen` | 0 | 2 | Orchestrator |
| `sp_Create_SRD_2_Interface` | 0 | 2 | Orchestrator |
| `sp_ATI_Korrektur` | 0 | 1 | gekoppelt |
| `sp_Bodensatz_konten` | 0 | 1 | gekoppelt |
| `sp_Check24_Antrag_Inaktivieren_Test` | 0 | 1 | gekoppelt |
| `sp_Create_Check24_OnBase_Test` | 0 | 1 | gekoppelt |
| `sp_Create_FinMgr_MasterDaten` | 0 | 1 | gekoppelt |
| `sp_Create_High_Watermarks_YtD` | 0 | 1 | gekoppelt |
| `sp_Create_Impairment_Test` | 0 | 1 | gekoppelt |
| `sp_Create_Kest_Befreiung_Test` | 0 | 1 | gekoppelt |
| `sp_Geldhandel_Check24_OnBase_Test` | 0 | 1 | gekoppelt |
| `sp_Kunden_Fluktuation_AdHoc` | 0 | 1 | gekoppelt |
| `sp_sperrquittierungen_Quartal` | 0 | 1 | gekoppelt |
| `sp_SupportNet_vs_YouTrack` | 0 | 1 | gekoppelt |
| `sp_Ueberziehungen_Test` | 0 | 1 | gekoppelt |
| `sp_ZVK_Eingang_Check24_Test` | 0 | 1 | gekoppelt |
| `sp_ZVK_Kontrakte_Ford_Verb` | 0 | 1 | gekoppelt |
| `sp_ZVK_Taeglich` | 0 | 1 | gekoppelt |

## Ressourcenabhängigkeiten je Prozedur

| Prozedur | Tabellen/Views (statisch erkannt) | DML | Dynamic SQL | Cursor | Compliance |
|---|---|---:|---:|---:|---:|
| `sp_Abgelaufene_US_Dokumente` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.Us_Doks`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Us_Doks` | ja | ja | nein | nein |
| `sp_Abgelaufene_Vollmachten` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.Vollmachten_Nur_Abgelaufen`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Vollmachten_Abgelaufen`, `Vollmachten_Aktiv`, `Vollmachten_Nur_Abgelaufen` | ja | ja | nein | nein |
| `sp_Ablaufende_Anleihen` | `Anleihen_Ablaufend`, `Anleihen_Tilgung`, `Betreuer`, `CRM`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Anleihen_Tilgung`, `ISIN`, `Kalender`, `Kunde`, `OPENQUERY`, `Sysobjects`, `TOBJ.WA00` | ja | ja | ja | nein |
| `sp_Ablaufende_Festgelder` | `Betreuer`, `Festgelder_Mature`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.GH`, `inbound.dbo.Festgelder_Mature`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | ja | nein |
| `sp_Aktive_Sperren_KD_KK` | `Abteilung`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DJ00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.Sperrcodes`, `inbound.dbo.Sperrcodes_KD_KK_IKS`, `Kalender`, `OPENQUERY`, `Sperrcodes`, `Sperrcodes_KD_KK_IKS`, `Sysobjects`, `THOBJ.SPTX` | ja | ja | ja | ja |
| `sp_AML_Meldung` | `AML_Meldung`, `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `OPENQUERY`, `Referenz`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_AML`, `ZVK_Compliance`, `ZVK_Compliance_Monat`, `ZVK_OPACC` | ja | ja | ja | ja |
| `sp_ATI_Investments` | `ATI_Analyse`, `ati_analyse`, `ATI_Einzahlungen`, `ati_investments`, `ATI_Investments`, `ATI_Investments_`, `ATI_Investments_VQ`, `ati_investments_VQ`, `ATI_Investoren`, `ati_investoren`, `ATI_Investoren_Roh`, `ATI_Korrektur`, `ATI_Unternehmenswerte`, `ati_unternehmenswerte`, `DECLARE`, `DUPLICATES`, `FMP_ATI_Extract`, `H000DTA.IE05`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.DP00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `Kalender`, `OPENQUERY`, `Produkt`, `Sysobjects`, `THOBJ.FX00`, `Vertrag` | ja | ja | ja | nein |
| `sp_ATI_Korrektur` | `ATI_Einzahlungen`, `ATI_Korrektur`, `Referenz`, `Sysobjects` | ja | nein | ja | nein |
| `sp_Bankbuch_Depotbestand` | `Depot_Bestand`, `Depotbestand_Bank_Buchungen`, `Depotbestand_Bank_Help`, `Depotbestand_Bank_Tag`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DA`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.ES`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.KP`, `inbound.dbo.Depot_Bestand`, `OPENQUERY`, `Sysobjects`, `Tagestabelle`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Bar_Transaktionen` | `Bar_Transaktionen`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.BT`, `H010DTA.KB`, `H010DTA.KK00`, `inbound.dbo.Bar_Transaktionen`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.FX00`, `THOBJ.LA00`, `THOBJ.PA`, `TOBJ`, `TOBJ.NACE` | ja | ja | ja | ja |
| `sp_BEPRO_Kondition` | `H010DTA.G000`, `inbound`, `inbound.dbo.BEPRO_Kondition`, `OPENQUERY`, `Sysobjects` | nein | nein | nein | nein |
| `sp_BO_Aenderungen` | `BO_Aenderungen`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.RV`, `inbound.dbo.BO_Aenderungen`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Bodensatz` | `Bodensatz`, `Bodensatz_2015`, `Bodensatz_Detail`, `Bodensatz_Konten`, `Bodensatz_Konten_VJ`, `Bodensatz_Salden`, `BV10DTA.K1`, `H000DTA.KD00`, `H010DTA.K1`, `H010DTA.KK00`, `Konto`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | nein | ja | nein |
| `sp_Bodensatz_konten` | `Bodensatz_Konten`, `Bodensatz_Konten_VJ`, `H000DTA.KD00`, `H010DTA.KK00`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | nein | nein | nein | nein |
| `sp_Buchung_GuV_Konto` | `GuV_Buchung`, `H010DTA.BT`, `H010DTA.ES`, `H010DTA.KB`, `H010DTA.KK00`, `H010PCT.DWKD_VORT`, `Inbound.dbo.GuV_Buchung`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX` | ja | ja | nein | nein |
| `sp_Check24_Antrag_Inaktivieren` | `C24_Antrag_Inaktivieren`, `H000DTA.GBPY00`, `inbound.dbo.C24_Antrag_Inaktivieren`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check24_Antrag_Inaktivieren_Test` | `C24_Antrag_Inaktivieren_test`, `H000DTATST.GBPY00`, `inbound.dbo.C24_Antrag_Inaktivieren_test`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check_603_vs_601` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H010DTA.KQ00`, `inbound.dbo.RPT_603_vs_601`, `OPENQUERY`, `RPT_603_vs_601`, `Sysobjects` | ja | nein | nein | ja |
| `sp_Check_Benchmark_VV_Kunden` | `Benchmark_VV_Kunden`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.O5`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.V000`, `H010DTA.VS00`, `inbound.dbo.Benchmark_VV_Kunden`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Check_CRS_Kontoregister` | `check_IBAN`, `Check_IBAN`, `IBAN_Cursor`, `IBAN_Depot_Kontrolle`, `Sysobjects`, `xml_Kontoregister`, `xml_Kontoregister_Kurz` | ja | nein | ja | ja |
| `SP_Check_Depot_Spesen_Konto` | `Depot_Spesen_Konto`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.Depot_Spesen_Konto`, `nofee_kunden`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | nein | nein | nein |
| `sp_check_Depots_Bestand` | `Check_Depot_All`, `Check_Depots_Protokoll`, `Falschbestand`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Check_Depots_Protokoll`, `Kalender`, `OPENQUERY`, `Sperr_Depots`, `Sysobjects` | ja | nein | ja | nein |
| `sp_Check_Doppelte_Kest_Tilgung` | `H000DTA.TINSTANCE`, `H000DTA.WS00`, `H010DTA.TG`, `H010DTA.tp`, `inbound.dbo.Kest_Tilgung`, `Kest_Tilgung`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Check_Eigenbestand_Lagerstelle` | `Eigenbestand_Lagerstellen`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DA`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Eigenbestand_Lagerstellen`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Check_Formular_Frequenz` | `16`, `18`, `Check_Formular_Frequenz`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.GBKD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.KV`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KQ00`, `H010DTA.VS00`, `inbound.dbo.Check_Formular_Frequenz`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Check_Jobs` | `Fehlerhafte_Jobs`, `inbound.dbo.Fehlerhafte_Jobs`, `inbound.dbo.Kalender`, `msdb.dbo.sysjobhistory`, `msdb.dbo.sysjobs`, `msdb.dbo.sysjobsteps`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Check_KAMA_Lieferungen` | `inbound.dbo.KAMA_Lieferungen_Treffer`, `IWPBPRD.H000DTA.IE05`, `Kalender`, `KAMA_Lieferungen`, `KAMA_Lieferungen_Treffer`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check_Konten_ohne` | `19`, `22`, `check_konten_ohne`, `Check_Konten_ohne`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H010DTA.KK00`, `H010DTA.KQ00`, `inbound.dbo.Check_Konten_ohne`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX` | ja | ja | nein | ja |
| `sp_Check_Konten_Zinsgruppe` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.KO00`, `inbound.dbo.Konten_Zinsgruppen`, `inbound.dbo.Kunden_Jahr`, `Kalender`, `Konten_Zinsgruppen`, `Kunden_Jahr`, `Neu_Kunden`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Check_Kreditkonten_neu` | `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.KK00`, `H010DTA.KQ00`, `inbound.dbo.Kreditkonten_neu`, `Kalender`, `Kreditkonten_neu`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Check_Kunden_Eroeffnungsdatum` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `inbound.dbo.Kunden_Konten_Prospectivs`, `Kalender`, `Konten_Prospectivs`, `Kunden_Konten_Prospectivs`, `Kunden_Prospectivs`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check_Kunden_mit` | `Check_Kunden_mit`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PF00`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KQ00`, `H010DTA.VS00`, `inbound.dbo.Check_Kunden_mit`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | ja |
| `sp_Check_Kunden_ohne` | `Check_Kunden_ohne`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PF00`, `H000DTA.PS00`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KQ00`, `H010DTA.VS00`, `inbound.dbo.Check_Kunden_ohne`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | nein | ja |
| `sp_Check_Kunden_Team_vs_CRM` | `CRM_vs_Team`, `H000DTA.AG00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.CRM_vs_Team`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Check_KundenProfil` | `Check_KundenProfil`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.PSFW00`, `H000DTA.SB00`, `inbound.dbo.Check_KundenProfil`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check_Kupon_Kest` | `H010DTA.KH`, `H010DTA.KP`, `inbound.dbo.Kupon_Kest_Check`, `Kalender`, `Kupon_Kest_Check`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Check_Kupon_Kest_Onbase` | `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.KH`, `H010DTA.KP`, `Kalender`, `Kupon_Kest_Onbase`, `Kupon_KESt_TAMBAS`, `MIFIR_Meldung_Onbase`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | ja |
| `sp_Check_LEI_Gueltigkeit` | `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `inbound.dbo.LEI_Gueltigkeit`, `LEI_Gueltigkeit`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | nein | nein | nein |
| `sp_Check_Mehrfache_Tin` | `Mehrfache_tin`, `Mehrfache_TIN`, `Sysobjects`, `TIN`, `Vergleich` | ja | nein | ja | nein |
| `sp_Check_MIFIR_Transaktionen` | `Auftraege_Ohne_MIFIR_Meldung`, `H000DTA.WSMI00`, `H000DTA.WSTR00`, `H010DTA.FTOD00`, `H010DTA.FTOM00`, `inbound.dbo.Auftraege_Ohne_MIFIR_Meldung`, `Kalender`, `MIFIR_Orders_All`, `MIFIRTRANSACTION`, `OPENQUERY`, `Order_All`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Check_Portfolio_Reports` | `Check_Reports`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H010DTA.KQ00`, `inbound.dbo.Check_Reports`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | nein | ja |
| `sp_Check_PTP_W10` | `Check_PTP_W10`, `H000DTA.WN00`, `H000DTA.WS00`, `inbound.dbo.Check_PTP_W10`, `Kalender`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Check_Quartalsspesen` | `Check_Spesen_Konto`, `Sysobjects` | nein | nein | nein | nein |
| `sp_Check_Relevante_Person` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR`, `H000DTA.PS00`, `inbound.dbo.Relevante_Person`, `Kalender`, `Kunde`, `OPENQUERY`, `Rel_pers`, `Rel_Pers`, `Relevante_Person`, `Sysobjects` | ja | ja | ja | nein |
| `sp_Check_REPP_vs_REKS` | `Check_TWR_VV_His`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.REPP`, `H010SAV.REKS`, `inbound.dbo.tb_Check_TWR_VV`, `Kalender`, `Kunde`, `OPENQUERY`, `Sysobjects`, `tb_Check_TWR_VV`, `THOBJ.FX00` | ja | ja | ja | nein |
| `sp_Check_Risk_Scoring` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RISC`, `H000DTA.RISC00`, `inbound.dbo.Risk_Scoring_Veraenderung`, `Kalender`, `Kunden_Cur`, `OPENQUERY`, `Risk_Scoring_Veraenderung`, `Risk_Scoring_Work`, `Sysobjects` | ja | ja | ja | ja |
| `sp_Check_SFTR_Valuation` | `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.DP00`, `H010DTA.REDP`, `inbound`, `inbound.dbo.SFTR_Valuation`, `Kalender`, `OPENQUERY`, `SFTR_Valuation`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Check_Smart_Invest` | `Check_Smart_Invest`, `FX_Kurse_Tgl`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.GH`, `inbound.dbo.Check_Smart_Invest`, `Kalender`, `Kunde`, `OPENQUERY`, `Position`, `Smart_Invest_Roh`, `Sysobjects`, `Team`, `THOBJ.FX00` | ja | ja | ja | nein |
| `SP_Check_Spesen_Konto` | `Check_Spesen_Konto`, `H000DTA.CF`, `H000DTA.GBKD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `inbound.dbo.Check_Spesen_Konto`, `inbound.dbo.NOFEE_Kunden`, `nofee_kunden`, `NOFEE_Kunden`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Check_TIN_Gueltigkeit` | `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.FATC00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PB00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `inbound`, `inbound.dbo.TIN_Gueltig`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `TIN`, `TIN_Gueltig` | ja | ja | ja | ja |
| `sp_Check_Vermittlerdaten_Controlling` | `Check_Vermittlerdaten_Controlling`, `controlling.dbo.Check_Vermittlerdaten_Controlling`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Check_VV_Tipas` | `Check_Tipas_Ausgabe`, `H000DTA.IE05`, `H000DTA.JG00`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.VS00`, `inbound.dbo.Check_Tipas_Ausgabe`, `OPENQUERY`, `Sysobjects`, `Tipas_user`, `Tipas_User`, `VV_Kunden` | ja | nein | nein | nein |
| `sp_Check_WP_Art_vs_Depot` | `Check_WP_Art_Depot`, `Commodities`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Check_WP_Art_Depot`, `OPENQUERY`, `Sperrdepots`, `Sysobjects`, `TOBJ.WA00` | ja | nein | ja | nein |
| `sp_Closed_clients_LMonth` | `Closed_clients`, `Closed_clients_Roh`, `closed_clients_roh`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.Closed_clients`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Create_Ablaufende_Garantien` | `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.GARA`, `inbound.dbo.OV_Garantien`, `Kalender`, `OPENQUERY`, `OV_Garantien`, `Sysobjects`, `THOBJ.PA` | ja | ja | nein | nein |
| `sp_Create_AML_Art5` | `AML_Art5_Meldung`, `AML_Art5_Roh`, `AML_Firmen_Art5`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.AML_Art5_Meldung`, `inbound.dbo.AML_Firmen_Art5`, `OPENQUERY`, `Summe`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00` | ja | ja | ja | ja |
| `sp_Create_ATIExport_UniCredit` | `ati_depots`, `ATI_Depots_Tambas`, `ATI_Depots_tambas`, `ATI_Export_Unicredit`, `ATI_Kunden`, `ATI_kunden`, `Clients_Cur`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.PFEV00`, `H000DTA.PS00`, `inbound`, `Kalender`, `mp_kunden`, `MP_Kunden`, `OPENQUERY`, `Sysobjects` | ja | ja | ja | nein |
| `sp_Create_Benutzergruppen_Menuepunkte` | `Benutzergruppen_Menuepunkte`, `inbound`, `Kunden_Cur`, `OPENQUERY`, `Sysobjects`, `THOBJ`, `THOBJ.A2` | ja | ja | ja | nein |
| `sp_Create_Best_Execution` | `Ausfuehrungen`, `Best_Ex_Ausfuehrungen`, `Best_Execution`, `best_execution`, `FX_Kurse_his`, `H000DTA.IE05`, `H000DTA.KDPR`, `H000DTA.NOKO00`, `H000DTA.NOTX00`, `H000DTA.PS00`, `H000DTA.PSFO`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.FTOD00`, `H010DTA.FTOM`, `H010DTA.FTOM00`, `H010DTA.FTON00`, `H010DTA.G000`, `H010DTA.TMIFIRTRANSACTION`, `H010DTA.VS00`, `H010DTA.WF`, `inbound.dbo.Best_Ex_Ausfuehrungen`, `inbound.dbo.Best_Execution`, `Kalender`, `Kontrakte`, `kontrakte`, `Kundenorder`, `Kundenorders`, `Liste_MICs`, `Liste_mics`, `Marktorder`, `MARKTORDERS`, `Marktorders`, `MIFIR`, `OPENQUERY`, `Standard_Spesen`, `Sysobjects`, `Teil_Ausfuehrungen`, `THOBJ.FX00`, `THOBJ.PA`, `TOBJ.WA00` | ja | ja | ja | ja |
| `sp_Create_Check24_Inaktiv_OnBase` | `C24_Inaktiv`, `Check24_Inaktiv_OnBase`, `H000DTATST.GBPY00`, `Inaktiv`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | ja | nein |
| `sp_Create_Check24_OnBase` | `C24_OnBase`, `Check24_Kunden_OnBase`, `Check24_Kunden_OnBase_test`, `H000DTA.CRSA00`, `H000DTA.GBKK00`, `H000DTA.IE`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PS00`, `H010DTA.KK00`, `H010DTA.Z200`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00` | ja | ja | nein | ja |
| `sp_Create_Check24_OnBase_Test` | `C24_OnBase_test`, `C24_OnBase_Test`, `Check24_Kunden_OnBase_test`, `Check24_Kunden_OnBase_Test`, `H000DTATST.CRSA00`, `H000DTATST.GBKK00`, `H000DTATST.IE`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.KV`, `H000DTATST.PS00`, `H010DTATST.KK00`, `H010DTATST.Z200`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJTST.FX00`, `THOBJTST.LA00` | ja | ja | nein | ja |
| `sp_Create_CRS_Listen` | `Ansaessigkeit_vs_AIA`, `CRS_Evidenzen`, `crs_kunden`, `CRS_Laender`, `crs_laender`, `CRS_Review`, `crs_review`, `DOK140_vs_Selbstauskunft`, `Entity_ohne_Rechtraeger`, `Entity_ohne_Selbstauskunft`, `Entity_Rechtraeger_spezial`, `Fehlende_GIIN`, `Fehlende_Selbstauskunft`, `Fehlende_TIN`, `Fehlender_CRS_Stamm`, `Kunden_Cur`, `Mehrfache_TIN`, `Status_ungeprueft`, `Sysobjects`, `vollmacht_lookup`, `Vollmacht_Lookup` | ja | nein | ja | ja |
| `sp_Create_CRS_Meldung_TPAM` | `CRS_Meldung_TPAM`, `CRS_TPAM`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `IBAN`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | ja | ja |
| `sp_Create_CRS_Review` | `CRS_Evidenzen`, `crs_evidenzen`, `CRS_Konten_Depots`, `CRS_KUNDEN`, `CRS_Kunden`, `CRS_Review`, `CRS_Vollmachten`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.CRSA00`, `H000DTA.CRSG00`, `H000DTA.FATC00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.CRS_Review`, `inbound.dbo.CRS_Vollmachten`, `inbound.dbo.Vollmacht_alle`, `inbound.dbo.Vollmacht_Lookup`, `Kunden_Cur`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA`, `THOBJ.VMVW`, `TOBJ`, `TOBJ.APBR`, `Vollmacht_Alle`, `Vollmacht_Cur`, `Vollmacht_Kunde`, `Vollmacht_Lookup` | ja | ja | ja | ja |
| `sp_Create_DatenExport_UniCredit` | `Clients_Cur`, `Depot_Cur`, `Export_Unicredit`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.DA`, `H010DTA.DP00`, `MP_Depots`, `MP_Depots_Tambas`, `mp_kunden`, `MP_Kunden`, `OPENQUERY`, `Sysobjects` | ja | nein | ja | nein |
| `sp_Create_ENR_Balances` | `BO_State`, `ENR_Balances`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.JL00`, `H000DTA.KD00`, `H000DTA.NOKO00`, `H000DTA.NOTX00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.DPSA02`, `H010DTA.G000`, `H010DTA.GH`, `H010DTA.JA00`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.ENR_Balances`, `Kalender`, `OPENQUERY`, `State`, `Sysobjects`, `THOBJ.LA00` | ja | ja | ja | nein |
| `sp_Create_ENR_Positions` | `ENR_Balances`, `ENR_Positions`, `H000DTA.IE05`, `H000DTA.JL00`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.DPSA02`, `H010DTA.JA00`, `H010DTA.KKSA`, `inbound.dbo.ENR_Positions`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Create_Evidenzen_OnBase` | `Dok`, `EV_OnBase`, `Evidenzen_OnBase`, `evidenzen_onbase`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `Kalender`, `Kunde`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `Zusatz_Dok` | ja | ja | ja | ja |
| `sp_Create_FinMgr_Bewegungen` | `AccountBooking`, `AccountBookingFee`, `AccountBookingfee`, `FinMgr_BuchTextSchluessel`, `FinMgr_Kest_Codes`, `FinMgr_KontoBuchungen`, `FinMgr_WP_Trades`, `H000DTA.CF`, `H000DTA.KD00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.BT`, `H010DTA.DP00`, `H010DTA.KB`, `H010DTA.KH`, `H010DTA.KK00`, `H010DTA.KP`, `H010DTA.LG`, `H010DTA.LP`, `H010DTA.TP`, `H010DTA.WF`, `inbound`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.SPAR00`, `trade`, `Trade`, `Trade_Fees`, `TradeFee`, `WP_Trans_Typen` | ja | ja | ja | nein |
| `sp_Create_FinMgr_MasterDaten` | `account`, `Account`, `Address`, `Address_Cur`, `Contact`, `Contact_Cur`, `Customer`, `customer`, `Customer_Cur`, `Depot`, `Depot_Disposal`, `DepotPosition`, `FinMgr_Depots`, `FinMgr_Festgelder`, `FinMgr_Konten`, `FinMgr_Kunden`, `FinMgr_Kurse_WU`, `FinMgr_Kurse_WUMA`, `FinMgr_MarketPlace`, `FinMgr_Portfolios`, `FinMgr_Position`, `FinMgr_User`, `FinMgr_User_Portfolios`, `FinMgr_Vollmachten`, `FinMgr_WP`, `Fixed_Deposit`, `FX_Kurse_his`, `FXRatio`, `Issuer`, `Kalender`, `Legitimation`, `Link_User_portfolio`, `Link_User_Portfolio`, `MarketNotation`, `MarketPlace`, `Portfolio_Cur`, `Price`, `Price_Wrk`, `Product`, `Product_Cur`, `Sysobjects`, `tipas_user_CPB`, `User_Daten` | ja | nein | ja | ja |
| `sp_Create_FMG_Plus_Positions` | `FMG_Plus_Export`, `FMG_Plus_Positions`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KA`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.FMG_Plus_Positions`, `OPENQUERY`, `Sysobjects`, `THOBJ.AB`, `THOBJ.FX00`, `TOBJ.WA00` | ja | nein | nein | nein |
| `sp_Create_goAML_Transactions` | `fx_kurse_his`, `goAML_Intern_Roh`, `goAML_Related_Persons`, `goAML_Related_Roh`, `goAML_Roh`, `goAML_Transaction`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.goAML_Related_Persons`, `inbound.dbo.goAML_Transaction`, `Kalender`, `OPENQUERY`, `Referenz`, `Referenz_Int`, `Related`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00` | ja | ja | ja | ja |
| `sp_Create_High_Volume_Kunden` | `H000DTA.CRSA00`, `H000DTA.CRSG00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DP00`, `Help_Table`, `High_Volume`, `High_Volume_`, `high_volume_20201231`, `High_Volume_BO`, `High_Volume_Gesamt`, `High_Volume_gesamt`, `Kalender`, `KUNDE`, `OPENQUERY`, `Sysobjects` | ja | ja | ja | ja |
| `sp_Create_High_Watermarks` | `fx_kurse_his`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.REPP`, `H010SAV.REKS`, `Kunden_Cur`, `OPENQUERY`, `Sysobjects`, `tb_volume`, `tb_Volume_TWR`, `tb_Volume_VV`, `tb_Volume_vv`, `tb_watermark`, `tb_Watermark`, `tb_Watermark_`, `THOBJ.FX00`, `viedb.inbound.dbo`, `Whg_Cur` | ja | ja | ja | nein |
| `sp_Create_High_Watermarks_YtD` | `fx_kurse_his`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.REPP`, `OPENQUERY`, `Sysobjects`, `tb_Volume`, `tb_volume`, `tb_Volume_Ytd`, `tb_Watermark_YtD`, `THOBJ.FX00`, `viedb.inbound.dbo` | ja | ja | nein | ja |
| `sp_Create_Impairment_Test` | `Impairment_Daten`, `impairment_test`, `Impairment_Test`, `Kalender`, `Rating_his`, `Sysobjects`, `Zinsen_his` | ja | nein | ja | ja |
| `sp_Create_IOMA_Portfolio` | `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KA`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.IOMA_Portfolio`, `IOMA_Export`, `IOMA_Portfolio`, `OPENQUERY`, `Sysobjects`, `THOBJ.AB`, `THOBJ.FX00`, `TOBJ.WA00` | ja | nein | nein | nein |
| `sp_Create_Kest_Befreiung` | `Depots`, `Firmen`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.CRSA00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.Z200`, `Kalender`, `Kest_Befreiung_`, `Kest_Befreiung_Aktuell`, `Kest_Befreiung_Depots`, `Kest_Befreiung_Firmen`, `Kest_Befreiung_Konten`, `Kest_Befreiung_quartal`, `Kest_Befreiung_Quartal`, `Kest_Befreiung_Quartal_alt`, `Kest_Befreiung_Treugeber_DP`, `Kest_Befreiung_Treugeber_KD`, `Kest_Befreiung_Treugeber_KT`, `Kest_befreiung_Upload`, `kest_befreiung_upload`, `Kest_Befreiung_Upload`, `Konten`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | ja | ja |
| `sp_Create_Kest_Befreiung_Test` | `auf`, `Depots`, `Firmen`, `H000DTATST.AD00`, `H000DTATST.CF`, `H000DTATST.CRSA00`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.PF00`, `H000DTATST.PFEV00`, `H000DTATST.PS00`, `H000DTATST.RV00`, `H010DTATST.DP00`, `H010DTATST.KK00`, `H010DTATST.Z200`, `inbound.dbo.Kalender`, `Kest_Befreiung_`, `Kest_Befreiung_Aktuell`, `Kest_Befreiung_Depots`, `Kest_Befreiung_Firmen`, `Kest_Befreiung_Konten`, `Kest_Befreiung_quartal`, `Kest_Befreiung_Quartal`, `Kest_Befreiung_Quartal_alt`, `Kest_Befreiung_Treugeber_DP`, `Kest_Befreiung_Treugeber_KD`, `Kest_Befreiung_Treugeber_KT`, `Kest_befreiung_Upload`, `kest_befreiung_upload`, `Kest_Befreiung_Upload`, `Konten`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | ja | ja |
| `sp_Create_Konto_saldo` | `H000DTA.KD00`, `H010DTA.KB`, `H010DTA.KK00`, `Kalender`, `KK_Saldo`, `Konto`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | ja | nein |
| `sp_Create_KPMG_Datenabzug` | `Buchungen`, `Buchungsjournal`, `H010DTA.BT`, `H010DTA.KB`, `H010DTA.KK00`, `H010PCT`, `OPENQUERY`, `Saldenliste`, `SaldenListe`, `Saldenliste_akt`, `Saldenliste_Akt`, `saldenliste_akt`, `Saldenliste_VJ`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Create_Kredit_Evidenzen_OnBase` | `Betreuer`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `inbound.dbo.KREV_Mail`, `Kalender`, `kalender`, `KREV_Mail`, `KREV_mail`, `KREV_OnBase`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Create_Kupon_Tilgung` | `H000DTA.KN00`, `H000DTA.PS`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.BT`, `H010DTA.KB`, `H010DTA.KK00`, `inbound.dbo.tb_Kupon_Tilgung`, `OPENQUERY`, `Sysobjects`, `tb_Kupon_Tilgung`, `Team`, `THOBJ.FX00` | ja | nein | ja | nein |
| `sp_Create_Manual_Risk_Review_Test` | `H000DTA.AD00`, `H000DTA.CRSG00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `IWPBPRD.H000DTA.BS00`, `IWPBPRD.H000DTA.IE05`, `Kalender`, `OPENQUERY`, `Risk_Review_OnBase_Test`, `RR_OnBase_Manual_Preview`, `sys.objects`, `T`, `THOBJ.PA`, `TOBJ` | ja | ja | nein | ja |
| `sp_Create_Onbase_Master_Data` | `Branchen_Codes_Onbase`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `Kunden_Kommunikation_Onbase`, `Meld_Branch_Codes_Onbase`, `NACE_Codes_Onbase`, `OPENQUERY`, `Sysobjects`, `Tambas_Controllinggruppe_Onbase`, `Tambas_Konditionen_Onbase`, `Tambas_Kundenprofil_Onbase`, `Tambas_Legitimationsquelle_Onbase`, `Tambas_Produkte_Onbase`, `Tambas_Rechtsformen_Onbase`, `THOBJ.KPFF00`, `THOBJ.KPFO00`, `THOBJ.KPFW00`, `THOBJ.NACL00`, `THOBJ.PA`, `TOBJ`, `TOBJ.APBR`, `TOBJ.NACE` | ja | ja | nein | nein |
| `sp_Create_Portfolio_Valuation` | `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KA`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.Portfolio_Valuation`, `OPENQUERY`, `Portfolio_Export`, `Portfolio_Valuation`, `Sysobjects`, `THOBJ.AB`, `THOBJ.FX00`, `TOBJ.WA00` | ja | nein | nein | nein |
| `sp_Create_QI_UM_Daten` | `dbo.QI_Meldung`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WN00`, `H000DTA.WS00`, `H010DTA.DP00`, `H010DTA.KH`, `H010DTA.KP`, `H010DTA.UM`, `OPENQUERY`, `QI_Meldung`, `Sysobjects`, `THOBJ.WN00`, `TOBJ.WA00` | ja | ja | nein | ja |
| `sp_Create_Raquest_Analyse` | `Ausgabe`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.K3`, `H010DTA.KH`, `H010DTA.KP`, `H010DTA.TP`, `OPENQUERY`, `Raquest_Analyse`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `TOBJ`, `WP_Kupons_Raquest`, `WP_Tilgungen_Raquest` | ja | ja | ja | nein |
| `sp_Create_Risk_Review_OnBase` | `H000DTA.AD00`, `H000DTA.CRSG00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H000DTA.SB00`, `Kalender`, `OPENQUERY`, `Risk_Review_OnBase`, `risk_review_onbase`, `RR_OnBase`, `Scoring_Cur`, `Sysobjects`, `THOBJ.PA`, `TOBJ` | ja | ja | ja | ja |
| `sp_Create_Risk_Review_OnBase_Test` | `H000DTA.AD00`, `H000DTA.CRSG00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H000DTA.RV00`, `H000DTA.SB00`, `IWPBPRD.H000DTA.BS00`, `IWPBPRD.H000DTA.IE05`, `Kalender`, `OPENQUERY`, `Risk_Review_OnBase`, `Risk_Review_OnBase_save`, `Risk_Review_OnBase_Test`, `Risk_Review_OnBase_test`, `Risk_Review_Vollmachten_Test`, `RR_OnBase_Test`, `RR_Vollmachten_Test`, `Scoring_Cur`, `Sysobjects`, `T`, `THOBJ.LA00`, `THOBJ.PA`, `TOBJ` | ja | ja | ja | ja |
| `sp_Create_SRD_2_Interface` | `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.FK00`, `H010DTA.VS00`, `H010DTA.VSKS00`, `Hauptkunden`, `Interface`, `OPENQUERY`, `srd2_anfrage`, `SRD2_Anfrage`, `SRD2_Help`, `SRD2_Kunden`, `SRD2_Kundendaten`, `SRD2_kundendaten`, `SRD2_Transaktionen`, `SRD_2_Interface`, `SRD_2_Thresholds`, `Subkunden`, `Sysobjects`, `THOBJ.LA00`, `Zeile` | ja | ja | ja | nein |
| `sp_Create_SRD_2_WP_Trans` | `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.LG`, `H010DTA.LP`, `H010DTA.WF`, `OPENQUERY`, `SRD2_Transaktionen`, `SRD_2_Thresholds`, `Sysobjects`, `THOBJ.LA00`, `WP_Trans_Typen` | ja | ja | nein | nein |
| `sp_Create_SupportNet_offen` | `Ansprechpartner`, `E_Mail`, `inbound.dbo.SN_Offen`, `inbound.dbo.sn_offen`, `inbound.dbo.sn_offen_vm`, `SN_Offen`, `sn_offen`, `SN_Offen_VM`, `SN_Offen_vm`, `Support_Net_Offen`, `Sysobjects` | ja | nein | ja | nein |
| `sp_Create_Swiss_Alpine_Balances` | `Alpine_Balances`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.GH`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.Alpine_Balances`, `inbound.dbo.Swiss_Balances`, `OPENQUERY`, `Swiss_Balances`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Create_Table_LaenderStamm` | `H000DTA.LAMA00`, `H010DTA.TISK00`, `Kalender`, `kalender`, `LaenderStamm`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `TISK_Laender`, `Tisk_Laender`, `ZVK_Laender`, `zvk_laender` | ja | ja | nein | ja |
| `sp_Create_Tambas_Assetera_Mapping` | `Assetera_Tambas_Mapping`, `Assetera_Tambas_WRK`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.PFEV00`, `H000DTATST.PS00`, `H010DTA.KK00`, `H010DTATST.KK00`, `inbound`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Create_TCM_Check_OnBase` | `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.ZVKK`, `inbound.dbo.TCM_Check_OnBase`, `Kalender`, `OPENQUERY`, `Sysobjects`, `TCM_Check`, `TCM_Check_OnBase`, `THOBJ.FX00`, `ZVK_TCM_Check`, `ZVK_TCM_Complete` | ja | ja | ja | ja |
| `sp_Create_Treasury_Listen` | `Check_Smart_Invest`, `Depotauszug_Treasury`, `Depotauszug_Treasury_Roh`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KM00`, `H000DTA.PS00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KOZC`, `H010DTA.ZI`, `H010PCT.DWKD`, `Kalender`, `Konto_Zinssaetze_Treasury`, `Konto_Zinssaetze_Treasury_Roh`, `Konto_Zinssaetze_Treasury_roh`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJFX00` | ja | ja | nein | nein |
| `sp_Create_Valorlife_Portfolio` | `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KA`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound`, `inbound.dbo.Valorlife_Portfolio`, `Kalender`, `Kunden_Depots_Monatsende`, `Kunden_Konten_Monatsende`, `OPENQUERY`, `Sysobjects`, `THOBJ.AB`, `THOBJ.FX00`, `TOBJ.WA00`, `Valorlife_Export`, `Valorlife_Portfolio` | ja | nein | nein | nein |
| `sp_Create_Verlustschwellenreport_Meldung` | `H000DTA.SB00`, `inbound.dbo.Verlustschwellenreports_Aktuell`, `IWPBPRD.H000DTA.IE05`, `IWPBPRD.H000DTA.KD00`, `IWPBPRD.H000DTA.PS00`, `IWPBPRD.H010DTA.TJ`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Verlustschwellenreports_Aktuell`, `Verlustschwellenreports_Gemeldet` | ja | ja | nein | nein |
| `sp_Create_WHVP_Balances` | `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.GH`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.WHVP_Balances`, `Kalender`, `OPENQUERY`, `Sysobjects`, `WHVP_Balances` | ja | ja | nein | nein |
| `sp_Create_WHVP_Trades` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.WF`, `inbound.dbo.WHVP_Trades`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `TOBJ.WA00`, `WHVP_trades`, `WHVP_Trades` | ja | ja | nein | nein |
| `sp_Create_WP_Trans_Historie` | `Abgang`, `Bewegung`, `EKW`, `EKW_ISIN`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.K3`, `H010DTA.KH`, `H010DTA.KP`, `H010DTA.LG`, `H010DTA.LP`, `H010DTA.LPAD00`, `H010DTA.TP`, `H010DTA.WF`, `inbound.dbo.Trans_History`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `TOBJ`, `Trans_History`, `WP_Trans_Typen`, `WP_Transaktionen_His` | ja | ja | ja | nein |
| `sp_Create_WPB_TCM_Clients` | `Clients_Cur`, `gold_master_Kunden`, `Gold_master_Kunden`, `LaenderStamm`, `sp_Create_WPB_TCM_Clients`, `Sysobjects`, `WPB_TCM_Clients` | ja | nein | ja | ja |
| `sp_Create_ZVK_Eingang_OnBase` | `FX_Kurse_Tgl`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.ZVKK`, `H010DTA.ZVKK00`, `Kalender`, `laenderstamm`, `LaenderStamm`, `OPENQUERY`, `Referenz`, `sperrcodes`, `Sperrcodes`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_Ein_OnBase`, `ZVK_Eingnge`, `ZVK_Schwellen_Onbase`, `zvk_schwellen_onbase`, `ZVK_Sperren`, `ZVK_Sperren_OnBase` | ja | ja | ja | ja |
| `sp_Create_ZVK_Master_Data` | `Compliance_Schwellenwerte`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DJ00`, `H010DTA.KK00`, `inbound`, `Kalender`, `laenderstamm`, `OPENQUERY`, `Sysobjects`, `THOBJ.BZ`, `THOBJ.FX00`, `THOBJ.PA`, `THOBJ.SPTX`, `ZVK_BLZ_BIC`, `ZVK_Kunden_Konten_Onbase`, `ZVK_Kunden_Onbase`, `ZVK_Schwellen_Onbase`, `ZVK_Sperren`, `zvk_sperren` | ja | ja | nein | ja |
| `sp_Create_ZVK_Valuta_OnBase` | `H010DTA.ZVKK00`, `inbound.dbo.ZVK_Valuta_OnBase`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `ZVK_Valuta`, `ZVK_Valuta_OnBase` | ja | ja | nein | nein |
| `sp_CRS_FATCA_Listen` | `CRS_Depot_Fluktuation`, `CRS_Konten_Fluktuation`, `CRS_Kunden_Fluktuation`, `CRS_Kunden_TPAM`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | nein | ja | nein | ja |
| `sp_Dauerauftraege_Privat` | `DA`, `Dauerauftr_Summe`, `Dauerauftraege`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DE00`, `H010DTA.KK00`, `inbound.dbo.Dauerauftr_Summe`, `inbound.dbo.Neukunden`, `Konto`, `neukunden`, `Neukunden`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Devisenhandel_Vontobel` | `Devisenhandel_Vontobel`, `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.DH`, `inbound.dbo.Devisenhandel_Vontobel`, `Kalender`, `Navigator_Orders_Year`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Dokumente` | `Dokumente`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `inbound.dbo.Dokumente`, `Kunden_Fluktuation`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.PA`, `TOBJ` | ja | nein | ja | nein |
| `sp_email_kundenvolumen_tipas_mailbox` | `12.06.2026`, `inbound.dbo.v_check_kundenvolumen_tipas_mailbox`, `Sysobjects` | ja | nein | nein | nein |
| `sp_email_neu_angelegte_anleihen` | `H000DTA.WN00`, `H000DTA.WS00`, `inbound.dbo.tb_anleihen_tmp`, `Kalender`, `OPENQUERY`, `Sysobjects`, `tb_anleihen_tmp` | nein | ja | nein | nein |
| `sp_email_wertpapiere_umbenennen_eng` | `H000DTA.WN00`, `H000DTA.WS00`, `inbound.dbo.tb_wertpapiere_tmp`, `OPENQUERY`, `Sysobjects`, `tb_wertpapiere_tmp` | nein | ja | nein | nein |
| `SP_ErinnerungsMail_Nachbuchen_erlaubt` | `Sysobjects` | nein | nein | nein | nein |
| `sp_ESG_Check` | `ESG_Check`, `ESG_Check_Roh`, `Feld_Cur`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.PSFW00`, `inbound`, `inbound.dbo.ESG_Check`, `Kalender`, `Kunde_Cur`, `OPENQUERY`, `Sysobjects`, `THOBJ.KPFF00` | ja | ja | ja | nein |
| `sp_Evidenzen_US_Dokumente` | `Evidenz_Us_Quest`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.Evidenz_Us_Quest`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `TOBJ` | ja | ja | nein | nein |
| `sp_EvidenzVerwaltung` | `Betreuer`, `Evidenzen`, `evidenzen`, `Evidenzen_Betreuer`, `evidenzen_Faellig`, `Evidenzen_Faellig`, `H000DTA.CF`, `H000DTA.GKEV00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DJ00`, `H010DTA.DP00`, `inbound.dbo.Evidenzen_Betreuer`, `inbound.dbo.Evidenzen_Faellig`, `Kalender`, `kunden_sperren`, `Kunden_Sperren`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.PA`, `THOBJ.SPTX` | ja | ja | ja | ja |
| `sp_FATCA_IA_Faellig` | `CRM`, `FATCA_IA`, `Fatca_IA`, `FATCA_IA_Auswertung`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.FATCA_IA_Auswertung`, `Kalender`, `OPENQUERY`, `Person`, `Sysobjects` | ja | ja | ja | ja |
| `sp_Fatca_Relevanz` | `Fatca_Relevanz`, `Fatca_Relevanz_Roh`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RV00`, `inbound.dbo.Fatca_Relevanz`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA` | ja | nein | nein | ja |
| `sp_Fehlerhafte_Corporate_Actions` | `CorpAction_Fehler`, `H010DTA.CANO`, `H010DTA.TIER`, `inbound.dbo.CorpAction_Fehler`, `Kalender`, `Kunden_Depots_aktuell`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Fehlerhafte_Quartalsspesen` | `Fehlerhafte_Quartalspesen`, `H000DTA.IE05`, `H000DTA.KD00`, `H010DTA.GBBR`, `H010DTA.VVBU`, `inbound.dbo.Fehlerhafte_Quartalspesen`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_findtext` | `c_foo`, `c_tabfields`, `syscolumns`, `syscomments`, `Sysobjects`, `sysobjects` | nein | nein | ja | nein |
| `sp_findtext_SP` | `c_foo`, `c_tabfields`, `Stored_procedures_found`, `syscolumns`, `syscomments`, `Sysobjects`, `sysobjects` | ja | nein | ja | nein |
| `sp_Firmen_Ablaufende_Vollmachten` | `Firmen_Ablaufende_Vollmachten`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RV00`, `inbound.dbo.Firmen_Ablaufende_Vollmachten`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Firmen_Fehlende_Vollmachten` | `Firmen_Fehlende_Vollmachten`, `Firmen_Fehlende_Vollmachten_OnBase`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `TOBJ`, `Vollmachten_Aktiv` | ja | ja | nein | nein |
| `sp_Firmen_ohne_BO` | `Firmen_ohne_BO`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.Firmen_ohne_BO`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_Firmen_Vollmachten` | `BO_Aenderungen`, `Firmen_Vollmachten`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.Firmen_Vollmachten`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `TOBJ` | ja | ja | nein | nein |
| `sp_Formulare_Inaktivieren` | `Formulare`, `H000DTA`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KQ00`, `inbound.dbo.Formulare`, `OPENQUERY`, `Sysobjects`, `THOBJ.FH` | ja | nein | nein | ja |
| `sp_Forwards_Mature` | `Betreuer`, `Forwards_all`, `Forwards_All`, `forwards_faellig`, `Forwards_Faellig`, `Forwards_Mature`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DH`, `inbound.dbo.Forwards_Mature`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | ja | nein |
| `sp_FX_Forwards` | `Evidenzen`, `Forwards`, `Forwards_Faellig`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DH`, `inbound.dbo.Forwards_Faellig`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.FX00` | ja | ja | ja | nein |
| `sp_FX_Kurse_His` | `FX_Kurse_his`, `FX_Kurse_work`, `H000DTA.WC`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_FX_Kurse_Taeglich` | `FX_Kurse_Tgl`, `FX_Kurse_Tgl_Save`, `H000DTA.WC`, `inbound.dbo.FX_Kurse_Tgl`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Geburtstagskinder` | `Betreuer`, `Geburtstagskinder`, `geburtstagskinder`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.geburtstagskinder`, `Kalender`, `OPENQUERY`, `Sysobjects` | ja | ja | ja | ja |
| `sp_Geldhandel_Check24_OnBase` | `C24_GH_OnBase`, `Check24_Geldhandel_OnBase`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H010DTA.GH`, `Kalender`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Geldhandel_Check24_OnBase_Test` | `C24_GH_OnBase_Test`, `Check24_Geldhandel_OnBase_Test`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.PS00`, `H010DTATST.GH`, `Kalender`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Gold_Kontrakte` | `Gold_Kontrakte`, `Gold_Kontrakte_4Med`, `Gold_Kontrakte_Contango`, `Gold_Kontrakte_Jahr`, `Gold_Vertriebspartner`, `gold_vertriebspartner`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.WF`, `inbound`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_Gold_Sparplaene` | `Gold_Sparplaene`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.NOKO00`, `H000DTA.NOTX00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound`, `inbound.dbo.Gold_Sparplaene`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_GW_Auswertungen` | `Depots`, `depots`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.KV`, `H000DTA.PF00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.PSFW00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSEM`, `H000DTA.WSSU`, `H010DTA.AZKR00`, `H010DTA.BT`, `H010DTA.CV00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.GARA00`, `H010DTA.GH`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.KKSA`, `H010DTA.KKSA02`, `H010DTA.KRST00`, `H010DTA.VS00`, `Konten`, `Kredite`, `Kreditkarten_Safes`, `KreditkartenUmsatz`, `Kunden`, `kunden`, `OPENQUERY`, `Sparbuecher`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.KPFF00`, `THOBJ.LA00`, `THOBJ.PA`, `TOBJ`, `TOBJ.WA00`, `WP_Transaktionen_His`, `ZVK_Kontrakte` | ja | nein | nein | ja |
| `sp_InvestorProfile` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.PSFW00`, `H000DTA.SB00`, `Investor_Profile`, `investor_profile`, `Investor_Profile_Roh`, `Kunde`, `OPENQUERY`, `Profil`, `Sysobjects`, `THOBJ.KPFF00` | ja | nein | ja | ja |
| `sp_Konten_Gueltigkeit` | `IWPBPRD.H010DTA.KK00`, `Konten_Gueltigkeit`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Konto_Abgleich_Valantic` | `Abgleich_Valantic`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `Kalender`, `KontoAbgleich_Valantic_Onbase`, `OPENQUERY`, `Sysobjects`, `Valantic_Konten` | ja | ja | nein | nein |
| `sp_Kontoregister_Kontrolle` | `Analyse_Cursor`, `Kontoregister_Kontrolle`, `Sysobjects`, `xml_Kontoregister` | ja | nein | ja | nein |
| `sp_Kredit_Unterschreitungen` | `FX_Kurse_Tgl`, `H000DTA.IE05`, `H000DTA.PS00`, `H010DTA.BG00`, `H010DTA.KK00`, `H010DTA.KKSA`, `H010DTA.KRST00`, `inbound.dbo.Unterschreitungen`, `Kalender`, `Kredit`, `Kredite_Sicherheiten`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `Unterschreitungen` | ja | ja | ja | nein |
| `sp_Kreditkarten_Monatlich` | `Detail`, `H000DTA.AD00`, `H000DTA.BS00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KM00`, `H000DTA.PF00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA`, `H010DTA.AZKR00`, `H010DTA.BG00`, `H010DTA.GARA00`, `H010DTA.KK00`, `inbound.dbo.Kreditkarten_Monatlich_Detail`, `Kalender`, `Kreditkarten_Monatlich`, `Kreditkarten_Monatlich_Detail`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA` | ja | nein | ja | nein |
| `sp_Kunden_Cash_Only` | `H000DTA.AD00`, `H000DTA.BS00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.KRST00`, `H010DTA.ZVKK00`, `inbound.dbo.Kunden_Cash_Only`, `inbound.dbo.Kunden_Cash_Only_BO`, `Kalender`, `Kunde`, `Kunden_Cash_Only`, `Kunden_Cash_Only_BO`, `Kunden_Cash_Only_roh`, `Kunden_Cash_Only_Roh`, `Kunden_Cash_Only_Umsatz`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA` | ja | ja | ja | ja |
| `sp_Kunden_Check_Compliance` | `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.LAMA00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.KRST00`, `inbound.dbo.Kunden_Check_Compliance`, `inbound.dbo.Laenderstamm_Check_Compliance`, `Kalender`, `Kunden_Check_Compliance`, `Laenderstamm_Check_Compliance`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00` | ja | ja | nein | ja |
| `sp_Kunden_Fluktuation` | `07.01.2025`, `16.04.2026`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `inbound.dbo.Kunden_Fluktuation`, `Kalender`, `Kunden_Fluktuation`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Kunden_Fluktuation_AdHoc` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `inbound.dbo.Kunden_Fluktuation_AdHoc`, `Kalender`, `Kunden_Fluktuation_AdHoc`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Kunden_Hochrisiko` | `H000DTA.AD00`, `H000DTA.BS00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.KRST00`, `H010DTA.WF`, `H010DTA.ZVKK00`, `inbound.dbo.Kunden_Hochrisiko`, `Kalender`, `Kunde`, `Kunden_Hochrisiko`, `Kunden_Hochrisiko_Cash_Umsatz`, `Kunden_Hochrisiko_DepotUmsatz`, `Kunden_Hochrisiko_Roh`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA` | ja | ja | ja | ja |
| `sp_Kunden_Loeschung_DSGVO` | `1.8.2025`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.Kunden_Loeschung`, `Kunden_loeschung`, `Kunden_Loeschung`, `OPENQUERY`, `Personen_Loeschung`, `Sysobjects` | ja | ja | nein | nein |
| `sp_kunden_ohne_volumen` | `H000DTA.IE05`, `OPENQUERY`, `Sysobjects`, `tb_work_kunden_ohne_volumen` | ja | nein | nein | nein |
| `sp_Kunden_Risikoaenderung` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RISC`, `inbound.dbo.Risiko_Aenderung`, `Kalender`, `Kunde`, `Kunden_Risiko_Aenderung_Roh`, `OPENQUERY`, `Risiko_Aenderung`, `Sysobjects`, `THOBJ.PA` | ja | ja | ja | ja |
| `sp_Kundenprofil_Depotbestand` | `Depot_Bestand`, `Depot_Bestand_Ausgabe`, `H000DTA.ALPF00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPF00`, `H000DTA.KDPR00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.PSFW00`, `H000DTA.RISC00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.VS00`, `inbound.dbo.Depot_Bestand`, `inbound.dbo.Depot_Bestand_Ausgabe`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `TOBJ.WA00`, `WP_Handel`, `WP_Lieferung` | ja | ja | nein | ja |
| `sp_Kundensperren_Compliance` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DJ00`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.Sperren_Compliance`, `OPENQUERY`, `Sperren_Compliance`, `Sysobjects` | ja | nein | nein | ja |
| `sp_Kupon_QI_Abstimmung` | `ABSTIMMUNG_`, `abstimmung_work`, `ABSTIMMUNG_Work`, `Eimal_Buchung`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.FK00`, `H010DTA.K3`, `H010DTA.KH`, `H010DTA.KP`, `Kalender`, `Kupon_His_QI`, `OPENQUERY`, `Order_NOK`, `QI_BV_`, `QI_BV_WORK`, `Sysobjects`, `THOBJ.FX00`, `TOBJ` | ja | ja | ja | ja |
| `sp_Kurscheck_Nostro_Bestand` | `H000DTA.WN00`, `H000DTA.WS00`, `H000DTA.WUMA`, `H010DTA.DB`, `inbound.dbo.Kurscheck_Nostro`, `ISIN`, `Kurscheck_Nostro`, `Kurscheck_Nostro_his`, `Kurscheck_Nostro_His`, `Kurse_WU`, `Kurse_WUMA`, `OPENQUERY`, `Sysobjects` | ja | ja | ja | nein |
| `sp_mail_test` | `inbound.dbo.WHVP_Balances`, `Sysobjects` | nein | nein | nein | nein |
| `sp_Mailbox_vs_Spesen` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `inbound.dbo.Mailbox_vs_Spesen`, `Kalender`, `Mailbox_vs_Spesen`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | nein |
| `sp_Mailing_Gruppen_Kunden` | `H000DTA.AD00`, `H000DTA.IA00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.Mailing_Gruppen_Kunden`, `Kunde`, `Mail`, `Mailing_Gruppen_Kunden`, `Mailing_Gruppen_Roh`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Mailing_Kunden` | `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IA00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.Mailing_Gruppen_Kunden`, `Kunde`, `Mail`, `Mailing_Gruppen_Kunden`, `Mailing_Gruppen_Roh`, `OPENQUERY`, `Sysobjects`, `THOBJ.LA00`, `THOBJ.PA` | ja | ja | ja | nein |
| `sp_Manuelle_WP_Kurse` | `H000DTA.NOKO00`, `H000DTA.NOTX00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H000DTA.WUMA00`, `H010DTA.DB`, `inbound.dbo.Manuelle_Kurse`, `kalender`, `manuelle_kurse`, `Manuelle_Kurse`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | nein |
| `sp_MIFID_Finanzinstrumente` | `H000DTA.WN00`, `H000DTA.WPDO00`, `H000DTA.WS00`, `H000DTA.WSEL00`, `H000DTA.WSKT00`, `H000DTA.WSMI00`, `H000DTA.WSPS00`, `H000DTA.WSTR00`, `H000DTA.WSZM00`, `H010DTA.DB`, `inbound.dbo.MIFID_Finanzinstrumente`, `MIFID_Finanzinstrumente`, `OPENQUERY`, `Sysobjects` | ja | nein | nein | ja |
| `sp_MIFID_II_BestEx_Offenlegung` | `H000DTA.IE00`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.PS00`, `H000DTA.WN00`, `H000DTA.WS00`, `H000DTA.WSMI00`, `H010DTA.FTOD00`, `H010DTA.FTOM00`, `H010DTA.WF`, `H010DTA.WG`, `MIFID_II_Best_Ex_Kontrakte`, `MIFID_II_Best_Ex_Orders`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `TOBJ.WMDO`, `TOBJ.ZQ` | nein | ja | nein | ja |
| `sp_MIFIR_Transaktionen_Onbase` | `H000DTA.WS00`, `H000DTA.WSMI00`, `H000DTA.WSSU`, `H000DTA.WSTR00`, `H010DTA.FTOD00`, `H010DTA.FTOM00`, `Kalender`, `MIFIR_Meldung_Onbase`, `MIFIR_Orders_Tambas`, `MIFIRTRANSACTION`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Neue_Wertpapiere` | `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `inbound.dbo.Neue_Wertpapiere`, `Kalender`, `Neue_Wertpapiere`, `neue_wertpapiere`, `OPENQUERY`, `Sysobjects`, `Team` | ja | ja | ja | ja |
| `sp_Neue_WPs_Ohne_ISIN` | `H000DTA.WN00`, `H000DTA.WS00`, `inbound.dbo.WP_ohne_ISIN`, `Kalender`, `OPENQUERY`, `Sysobjects`, `WP_ohne_ISIN` | ja | ja | nein | ja |
| `sp_NeuKunden_Sutor` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.KK00`, `inbound.dbo.NeuKunden_Sutor`, `Kalender`, `NeuKunden_Sutor`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_OENB_MELDUNG_RU_BY` | `FX_Kurse_his`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PP00`, `H000DTA.PS00`, `H010DTA.DJ00`, `H010DTA.DP00`, `H010DTA.KK00`, `H010SAV.REKS`, `Kunde`, `OENB_Kunden_RU_BY`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `TOBJ` | ja | ja | ja | ja |
| `sp_Offene_Orders` | `Betreuer`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.FTOD00`, `inbound.dbo.Offene_Orders`, `Kalender`, `Offene_Orders`, `OPENQUERY`, `Sysobjects`, `THOBJ.PA`, `TOBJ` | ja | ja | ja | nein |
| `sp_Options` | `Betreuer`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Optionen_Mature`, `OPENQUERY`, `Optionen`, `Optionen_Mature`, `Sysobjects` | ja | nein | ja | nein |
| `sp_Orders_via_Navigator` | `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.FTOD00`, `inbound.dbo.Navigator_Orders_Quater`, `Kalender`, `Navigator_Orders_Quater`, `Navigator_Orders_Year`, `OPENQUERY`, `Sysobjects` | ja | ja | nein | nein |
| `sp_OTC_Dokumente` | `Betreuer`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `inbound.dbo.OTC_Dokumente`, `OPENQUERY`, `OTC_Dokumente`, `Sysobjects`, `Team`, `THOBJ.PA`, `TOBJ` | ja | nein | ja | ja |
| `sp_Professionelle_Kunden` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `inbound.dbo.Prof_Kunden`, `OPENQUERY`, `Prof_Kunden`, `Sysobjects`, `Team` | ja | nein | ja | nein |
| `sp_Quest_Auswertung` | `inbound`, `Max_Date`, `OPENQUERY`, `Quest_Auswertung`, `Sysobjects`, `THOBJ.LA00`, `TOBJ` | ja | ja | ja | nein |
| `sp_Read_Impairment_Daten` | `FX_Kurse_Tgl`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDRA`, `H000DTA.KDRA00`, `H000DTA.KM00`, `H000DTA.PS00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H000DTA.ZIGR`, `H010DTA.DA`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.GH`, `H010DTA.K1`, `H010DTA.KK00`, `H010DTA.KKSA`, `H010DTA.KO00`, `H010DTA.KRST00`, `H010DTA.ZI`, `H010DTA.ZUSK00`, `Impairment_Daten`, `Kalender`, `Konten_Wrk`, `OPENQUERY`, `Rating_his`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `THOBJ.PA`, `TOBJ.APBR`, `Zinsen_his` | ja | ja | nein | ja |
| `sp_Read_Tambas_Daten_FinMgr` | `FinMgr_Depots`, `FinMgr_Festgelder`, `FinMgr_Kest_Codes`, `FinMgr_Konten`, `FinMgr_Kunden`, `FinMgr_Kurse_WUMA`, `FinMgr_MarketPlace`, `FinMgr_Portfolios`, `FinMgr_Position`, `FinMgr_User`, `FinMgr_User_Portfolios`, `FinMgr_Vollmachten`, `FinMgr_WP`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.JG00`, `H000DTA.JH00`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H000DTA.WN00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H000DTA.WUMA`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.GH`, `H010DTA.JA00`, `H010DTA.KK00`, `H010DTA.VS00`, `Kalender`, `LegalForm_Mapping`, `OPENQUERY`, `STHOBJ.PA`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `THOBJ.PA`, `TOBJ`, `TOBJ.WA00` | ja | ja | nein | nein |
| `sp_Realisierte_Konten` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.Realisierte_Konten`, `OPENQUERY`, `Realisierte_Konten`, `Sysobjects`, `THOBJ.FX00` | ja | nein | nein | nein |
| `sp_Review_Nostro_Bestsand_Risk` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDRA00`, `H000DTA.PS00`, `H000DTA.WN`, `H000DTA.WS00`, `H000DTA.WUMA00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.PX`, `Inbound.dbo.Nostro_Bestand_Risk`, `Kalender`, `Nostro_Best_Risk_Roh`, `Nostro_Bestand_Risk`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | ja |
| `sp_Risikoklasse_Durchschnitt_VV` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDPR00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KKSA`, `H010DTA.VS00`, `inbound.dbo.VV_Kundenvolumen`, `Kunde_Cur`, `OPENQUERY`, `Position_Cur`, `Sysobjects`, `THOBJ.PA`, `VV_Kundenvolumen` | ja | nein | ja | ja |
| `sp_Risk_OENB` | `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDRA00`, `H000DTA.KM00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.K1`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.KKSA`, `H010DTA.KRST00`, `H010DTA.ZI`, `Kalender`, `OENB_Risk_Auswertung`, `OENB_Risk_Daten`, `OENB_Risk_Konzern`, `OPENQUERY`, `risk_kunden`, `Risk_Kunden`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.PA`, `TOBJ.NACE` | ja | ja | nein | ja |
| `sp_Risk_Review_Abgeschlossen` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H000DTA.SB00`, `H010DTA.DJ00`, `inbound.dbo.RR_Erledigt`, `Kalender`, `OPENQUERY`, `RR_Erledigt`, `Sysobjects`, `THOBJ.PA` | ja | ja | nein | ja |
| `sp_Risk_Review_Check` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.DJ00`, `inbound.dbo.RR_Check`, `Inbound.dbo.RR_Check`, `Kalender`, `OPENQUERY`, `Risk_Review_Check_OnBase`, `rr_check`, `RR_Check`, `Sysobjects`, `THOBJ.PA`, `viedb.inbound.dbo` | ja | ja | nein | ja |
| `sp_Risk_Review_Offen` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H000DTA.SB00`, `Kalender`, `OPENQUERY`, `Risk_Review_OnBase`, `RR_Offen`, `rr_offen`, `Sysobjects` | ja | ja | nein | ja |
| `sp_Risk_Review_OnBase_Details` | `Client_Cur`, `H000DTA.BS00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.NOKO00`, `H000DTA.PF00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RIRW00`, `H010DTA.DP00`, `Kalender`, `OPENQUERY`, `Risk_Review_Details_OnBase`, `RR_Details_OnBase1`, `RR_Details_OnBase2`, `Sysobjects`, `THOBJ.PA` | ja | ja | ja | ja |
| `sp_Risk_Review_OnBase_Transaktionen` | `Counting_Cur`, `FX_Kurse_his`, `H000DTA.IE05`, `H000DTA.RISC00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `OPENQUERY`, `RiskReview_Trans_OnBase`, `RiskReview_Transaktionen`, `RR_Trans_Work`, `Sysobjects`, `THOBJ.FX00` | ja | ja | ja | ja |
| `sp_RiskScoring_Kontrolle` | `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.PSFW00`, `H000DTA.RISC00`, `H000DTA.SB00`, `inbound.dbo.RiskScoring_Quer`, `Kunde_Cur`, `OPENQUERY`, `riskscoring`, `RiskScoring`, `RiskScoring_Quer`, `Sysobjects`, `THOBJ.KPFF00`, `THOBJ.KPFO00`, `THOBJ.LA00`, `THOBJ.PA` | ja | nein | ja | ja |
| `sp_RiskScoring_Onbase` | `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.PSFW00`, `H000DTA.RISC00`, `H000DTA.SB00`, `Kunde_Cur`, `OPENQUERY`, `RiskScoring_OnBase`, `RiskScoring_Quer_Onbase`, `RiskScoring_Quer_OnBase`, `Sysobjects`, `THOBJ.KPFF00`, `THOBJ.KPFO00`, `THOBJ.LA00`, `THOBJ.PA` | ja | ja | ja | ja |
| `sp_Salden_KO_Sperre_CS` | `CS_Salden`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H010DTA.DJ00`, `H010DTA.KK00`, `inbound`, `inbound.dbo.CS_Salden`, `inbound.dbo.KO_Salden`, `KO_Salden`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00` | ja | ja | nein | ja |
| `sp_Send_SRD_2_CSV` | `H000DTA.AD00`, `H000DTA.KN00`, `inbound.dbo.SRD_2_Interface`, `OPENQUERY`, `SRD2_Anfrage`, `SRD2_Transaktionen`, `SRD_2_Interface`, `SRD_2_Thresholds`, `Sysobjects`, `User_eMail` | nein | ja | nein | nein |
| `sp_sperraenderung` | `Detail`, `H000DTA.IE05`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.DJ`, `H010DTA.DP00`, `H010DTA.KK00`, `inbound.dbo.Sperr_Aenderungen`, `Kalender`, `OPENQUERY`, `Sperr_Aenderungen`, `Sperr_Aenderungen_Onbase`, `Sperr_Aenderungen_Onbase_VJ`, `Sperraenderungen`, `Sysobjects`, `THOBJ.SPTX` | ja | ja | ja | ja |
| `sp_sperrquittierungen` | `Betreuer`, `FX_Kurse_His`, `H000DTA.A100`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.DQSP00`, `H010DTA.FTOD00`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.Sperrquittierungen_Faellig`, `Kalender`, `OPENQUERY`, `Sperrquittierungen`, `Sperrquittierungen_Faellig`, `Sperrquittierungen_Onbase`, `Sperrquittierungen_OnBase`, `Sperrquittierungen_Onbase_VJ`, `Sysobjects`, `Team`, `THOBJ.FX00`, `THOBJ.SPTX`, `Woche` | ja | ja | ja | ja |
| `sp_sperrquittierungen_Quartal` | `H000DTA.A100`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.DQSP00`, `H010DTA.KK00`, `inbound.dbo.Sperrquittierungen_IKS`, `Kalender`, `OPENQUERY`, `Sperrquittierungen_IKS`, `Sperrquittierungen_IKS_Roh`, `Sysobjects`, `Team`, `THOBJ.SPTX` | ja | ja | ja | ja |
| `sp_SupportNet_vs_YouTrack` | `Import`, `SN_Offen`, `sn_offen`, `Support_Net_Offen`, `SupportNet_vs_YouTrack`, `Sysobjects`, `youtrack_issues`, `Youtrack_Issues` | ja | nein | ja | nein |
| `sp_test` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PS00`, `H000DTA.SB00`, `OPENQUERY`, `Sysobjects`, `test_1`, `Test_1` | nein | nein | nein | nein |
| `sp_Ueberziehungen` | `FX_Kurse_Tgl`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KM00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.K1`, `H010DTA.KK00`, `H010DTA.KKSA`, `inbound.dbo.Ueberziehungen`, `Inbound.dbo.Ueberziehungen_Summen`, `inbound.dbo.Ueberziehungen_Summen`, `Kalender`, `Konto`, `Kunde`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `ueberziehungen`, `Ueberziehungen`, `Ueberziehungen_Summen`, `Ueberziehungen_VW` | ja | ja | ja | ja |
| `sp_Ueberziehungen_Test` | `FX_Kurse_Tgl`, `H000DTATST.CF`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.KM00`, `H000DTATST.PS00`, `H000DTATST.SB00`, `H010DTA.K1`, `H010DTA.KK00`, `H010DTATST.KK00`, `H010DTATST.KKSA`, `inbound.dbo.Ueberziehungen`, `inbound.dbo.Ueberziehungen_Summen_TEST`, `Inbound.dbo.Ueberziehungen_Summen_TEST`, `Kalender`, `Konto`, `Kunde`, `OPENQUERY`, `Sysobjects`, `THOBJTST.FX00`, `ueberziehungen`, `Ueberziehungen`, `Ueberziehungen_Summen_TEST`, `Ueberziehungen_TEST`, `Ueberziehungen_TTEST`, `Ueberziehungen_VW_TEST` | ja | ja | ja | ja |
| `sp_Vollmacht_Sperren` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.DJ00`, `inbound.dbo.Vollmachten_Sperren`, `OPENQUERY`, `Sysobjects`, `Vollmachten_Sperren`, `Vollmachten_Sperren_roh`, `Vollmachten_Sperren_Roh` | ja | nein | nein | ja |
| `sp_Vollmachten_PEP` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.RV00`, `H000DTA.SB00`, `inbound.dbo.Vollmachten_PEP`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Vollmachten_PEP` | ja | ja | nein | ja |
| `sp_VST_9999800011_Gegenbuchung` | `H010DTA.BT`, `H010DTA.ES`, `H010DTA.KB`, `H010DTA.KK00`, `H010PCT.DWKD_VORT`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX`, `VST_Gegenbuchung` | nein | ja | nein | nein |
| `sp_VV_Depot_Check` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.DP00`, `H010DTA.VS00`, `inbound.dbo.VV_Depot_Check`, `OPENQUERY`, `Sysobjects`, `VV_Depot_Check` | nein | ja | nein | nein |
| `sp_VV_IP_AenderungAnlage` | `Check_Konten_ohne`, `H000DTA.A100`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.RV00`, `H000DTA.SB00`, `H010DTA.VS00`, `inbound.dbo.VV_IP_AenderungAnlage`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.VMVW`, `VV_IP_AenderungAnlage` | nein | ja | nein | ja |
| `sp_WP_Bewegungen` | `H000DTA.CF`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.WSSU`, `H010DTA.DB`, `H010DTA.DP00`, `H010DTA.LP`, `H010DTA.LPAD00`, `H010DTA.WF`, `inbound.dbo.WP_Bewegungen_VM`, `Kalender`, `OPENQUERY`, `Sysobjects`, `wp_bestand`, `WP_Bestand`, `WP_Bewegungen_VM` | ja | ja | nein | nein |
| `sp_WP_Kontrakte_Taeglich` | `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.WF`, `inbound.dbo.WP_Kontrakte_Tgl`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `WP_Kontrakte_Tgl` | ja | ja | nein | nein |
| `sp_WP_Orders` | `H000DTA.IE05`, `H000DTA.KDPR00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.PSFO00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.FTOD00`, `H010DTA.VS00`, `inbound.dbo.WP_Orders_Betreuer`, `inbound.dbo.WP_Orders_Quater`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.PA`, `TOBJ.$C`, `wp_Orders`, `WP_Orders`, `WP_Orders_Betreuer`, `wp_Orders_Quater`, `WP_Orders_Quater` | ja | ja | ja | ja |
| `sp_WP_Trans_Check` | `FX_Kurse_Tgl`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H000DTA.WSSU`, `H010DTA.DP00`, `H010DTA.WF`, `inbound.dbo.WP_Trans_Check`, `Kalender`, `Kauf`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `WP_Trans_Check`, `WP_Trans_His_Work` | ja | ja | ja | ja |
| `sp_Write_Kunden_MonatsendDaten` | `FX_Kurse_his`, `FX_Kurse_Tgl`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PF00`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.REPP`, `H010DTA.TJ`, `H010DTA.VS00`, `inbound`, `inbound.dbo.VV_Kunden_Volumen_Drk`, `Kalender`, `Kunden_Belege`, `Kunden_Depots_Aktuell`, `Kunden_Depots_aktuell`, `Kunden_Depots_Monatsende`, `Kunden_Konten_Aktuell`, `Kunden_Konten_aktuell`, `Kunden_konten_Monatsende`, `Kunden_Konten_Monatsende`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `Volumina`, `VV_Kunden_Tages_Volumen`, `VV_Kunden_Volumen_Drk` | ja | ja | ja | nein |
| `sp_Write_Kunden_Postfach` | `Betreuer`, `Detail`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.JA00`, `inbound.dbo.Kunden_Postfach`, `inbound.dbo.Summe_Kunden_Postfach`, `Kunden_Postfach`, `OPENQUERY`, `Summe_Kunden_Postfach`, `Sysobjects`, `Team` | ja | nein | ja | nein |
| `sp_Write_Kunden_Salden` | `Betreuer`, `Datum`, `FX_Kurse_Tgl`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KV`, `H000DTA.PS00`, `H000DTA.SB00`, `H000DTA.WN00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DB`, `H010DTA.DP00`, `inbound.dbo.Kunden_Fonds_Eigen`, `inbound.dbo.Kunden_Salden`, `inbound.dbo.Summe_Fonds_Eigen`, `inbound.dbo.Summe_Fonds_Eigen_LM`, `inbound.dbo.Summe_Salden`, `Kunden_Fonds_Eigen`, `Kunden_Salden`, `kunden_salden`, `OPENQUERY`, `Summe_Fonds_Eigen`, `Summe_Fonds_Eigen_LM`, `Summe_Salden`, `Sysobjects`, `Team`, `THOBJ.FX00`, `WP` | ja | nein | ja | nein |
| `sp_Write_Kunden_Sprache` | `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `Kunden_Sprache`, `OPENQUERY`, `Sysobjects` | nein | ja | nein | nein |
| `sp_Write_Kundenstamm` | `03.04.2026`, `04.01.2024`, `08.01.2021`, `23.04.2026`, `30.09.2021`, `H000DTA.AD00`, `H000DTA.CF`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.VS00`, `Kundenstamm`, `OPENQUERY` | ja | nein | nein | nein |
| `sp_Write_Treasury_Salden` | `Depotauszug_Treasury`, `Depotauszug_Treasury_roh`, `Depotauszug_Treasury_Roh`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KM00`, `H000DTA.PS00`, `H000DTA.WS00`, `H010DTA.DA`, `H010DTA.DP00`, `H010DTA.KK00`, `H010DTA.KKSA`, `H010DTA.KOZC`, `H010DTA.ZI`, `H010PCT.DWKD`, `inbound.dbo.Treasury_Salden`, `Kalender`, `Konto_Zinssaetze_Treasury`, `Konto_Zinssaetze_Treasury_Roh`, `OPENQUERY`, `Sysobjects`, `THOBJ.BI`, `THOBJ.FX00`, `THOBJ.LA00`, `Treasury_Salden` | ja | ja | nein | nein |
| `sp_ZVK_Ausgaenge_OnBase` | `FX_Kurse_Tgl`, `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.ZVKK`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `THOBJ.PA`, `ZVK_Aus_OnBase`, `ZVK_Ausgaenge` | ja | ja | nein | ja |
| `sp_ZVK_Compliance` | `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KDRA00`, `H000DTA.PS00`, `H000DTA.PSFW00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.ZVK_Compliance_Monat`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_Compliance`, `ZVK_Compliance_Monat`, `ZVK_OPACC` | ja | ja | nein | ja |
| `sp_ZVK_Eingang_Check24` | `Betreuer`, `Check24_Int_Rates_OnBase`, `Check24_Kunden_OnBase`, `H000DTA.GBKK00`, `H000DTA.GBPY00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.ZVK_C24_Taeglich`, `kalender`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `ZVK_C24_Taeglich`, `ZVK_Eingang_C24` | ja | ja | ja | nein |
| `sp_ZVK_Eingang_Check24_Test` | `Betreuer`, `Check24_Int_Rates_OnBase`, `Check24_Int_Rates_OnBase_Test`, `Check24_Kunden_OnBase_Test`, `H000DTATST.GBKK00`, `H000DTATST.GBPY00`, `H000DTATST.IE05`, `H000DTATST.KD00`, `H000DTATST.KN00`, `H000DTATST.PS00`, `H000DTATST.SB00`, `H010DTATST.KK00`, `H010DTATST.ZVKK00`, `inbound.dbo.ZVK_C24_Taeglich_Test`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJTST.FX00`, `ZVK_C24_Taeglich_Test`, `ZVK_Eingang_C24_Test` | ja | ja | ja | nein |
| `sp_ZVK_Kontrakte` | `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.ZVK_Kontrakte_Quater`, `Kalender`, `OPENQUERY`, `Sysobjects`, `Team`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_Kontrakte`, `ZVK_Kontrakte_Quater` | ja | ja | ja | nein |
| `sp_ZVK_Kontrakte_Ford_Verb` | `H000DTA.IE05`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.ZVK_Kontrakte_RW`, `Kalender`, `OPENQUERY`, `Sysobjects`, `THOBJ.FX00`, `ZVK_Kontrakte_RW` | ja | ja | nein | nein |
| `sp_ZVK_RU_BY_UA` | `H000DTA.AD00`, `H000DTA.IE05`, `H000DTA.KD00`, `H000DTA.PFEV00`, `H000DTA.PP00`, `H000DTA.PS00`, `H000DTA.RV00`, `H010DTA.VS00`, `Inbound.dbo.Kunden_RU_UA_BY`, `inbound.dbo.Personen_RU_UA_BY`, `inbound.dbo.ZVK_RU_BY_UA`, `Kunde`, `Kunden_RU_UA_BY`, `Kunden_RU_UA_BY_VW`, `LaenderStamm`, `OPENQUERY`, `Person`, `Personen_RU_UA_BY`, `Personen_RU_UA_BY_VW`, `Referenz`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.LA00`, `Vollmacht`, `ZVK_Kontrakte_Tgl`, `ZVK_RU_BY_UA` | ja | ja | ja | ja |
| `sp_ZVK_Sepa_Ausgaenge_OnBase` | `21.05.2026`, `H010DTA.ZVKK00`, `Kalender`, `OPENQUERY`, `Sysobjects`, `ZVK_Sepa_Aus_OnBase`, `ZVK_SEPA_Ausgaenge` | ja | ja | nein | nein |
| `sp_ZVK_Taeglich` | `Betreuer`, `H000DTA.IE05`, `H000DTA.KN00`, `H000DTA.PFEV00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.DJ00`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `inbound.dbo.Payments_Campione`, `inbound.dbo.Payments_ENR`, `inbound.dbo.Payments_WHVP`, `inbound.dbo.ZVK_Eingang_Sanktionen`, `inbound.dbo.ZVK_Eingang_Sepa`, `inbound.dbo.ZVK_Kontrakte_Taeglich`, `inbound.dbo.ZVK_Kontrakte_Tgl`, `Kalender`, `LaenderStamm`, `OPENQUERY`, `Payments_Campione`, `Payments_ENR`, `Payments_WHVP`, `Sysobjects`, `Team`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_Eingang_Sanktionen`, `ZVK_Eingang_Sepa`, `ZVK_Kontrakte_Taeglich`, `ZVK_Kontrakte_Tgl` | ja | ja | ja | ja |
| `sp_ZVK_VS_Schwellenwert` | `H000DTA.IE05`, `H000DTA.KDRA00`, `H000DTA.PS00`, `H000DTA.RISC00`, `H000DTA.SB00`, `H010DTA.KB`, `H010DTA.KK00`, `H010DTA.ZVKK00`, `Kalender`, `Kunde`, `OPENQUERY`, `Referenz`, `Sysobjects`, `THOBJ.FX00`, `THOBJ.PA`, `ZVK_Schwellen_Onbase`, `ZVK_Schwellwerte_Perioden_OnBase`, `ZVK_VS_Schwellen`, `ZVK_VS_Schwellen_Roh` | ja | ja | ja | ja |

## Risikofokus und Empfehlungen

1. **Direkte Aufrufkanten validieren:** Für jede Kante Tests bzw. Deployment-Reihenfolge dokumentieren.
2. **Dynamic SQL priorisieren:** Parameterbindung über `sp_executesql` prüfen und Eingaben/Objektnamen whitelisten.
3. **DML-Knoten schützen:** Transaktionsgrenzen, Fehlerbehandlung, Berechtigungen und Regressionstests prüfen.
4. **Zentrale Prozeduren zuerst analysieren:** Änderungen an Knoten mit vielen eingehenden Aufrufen haben den größten Blast Radius.
5. **Statische Grenzen beachten:** SQL-Agent-Jobs, externe Anwendungen, Synonyme, Views, Linked Server und dynamisch zusammengesetzte Prozedurnamen separat gegen die Datenbankmetadaten abgleichen.

## Vollständiges Prozedur-Inventar

| Prozedur | Datei | Zeilen | Eingehend | Ausgehend |
|---|---|---:|---:|---:|
| `sp_Abgelaufene_US_Dokumente` | `q1/Queries/Procedures/sp_Abgelaufene_US_Dokumente.sql` | 235 | 0 | 0 |
| `sp_Abgelaufene_Vollmachten` | `q1/Queries/Procedures/sp_Abgelaufene_Vollmachten.sql` | 246 | 0 | 0 |
| `sp_Ablaufende_Anleihen` | `q1/Queries/Procedures/sp_Ablaufende_Anleihen.sql` | 306 | 0 | 0 |
| `sp_Ablaufende_Festgelder` | `q1/Queries/Procedures/sp_Ablaufende_Festgelder.sql` | 181 | 0 | 0 |
| `sp_Aktive_Sperren_KD_KK` | `q1/Queries/Procedures/sp_Aktive_Sperren_KD_KK.sql` | 392 | 0 | 0 |
| `sp_AML_Meldung` | `q1/Queries/Procedures/sp_AML_Meldung.sql` | 225 | 0 | 0 |
| `sp_ATI_Investments` | `q1/Queries/Procedures/sp_ATI_Investments.sql` | 518 | 1 | 0 |
| `sp_ATI_Korrektur` | `q1/Queries/Procedures/sp_ATI_Korrektur.sql` | 74 | 0 | 1 |
| `sp_Bankbuch_Depotbestand` | `q1/Queries/Procedures/sp_Bankbuch_Depotbestand.sql` | 495 | 0 | 0 |
| `sp_Bar_Transaktionen` | `q1/Queries/Procedures/sp_Bar_Transaktionen.sql` | 308 | 0 | 0 |
| `sp_BEPRO_Kondition` | `q1/Queries/Procedures/sp_BEPRO_Kondition.sql` | 42 | 0 | 0 |
| `sp_BO_Aenderungen` | `q1/Queries/Procedures/sp_BO_Aenderungen.sql` | 157 | 0 | 0 |
| `sp_Bodensatz` | `q1/Queries/Procedures/sp_Bodensatz.sql` | 470 | 1 | 0 |
| `sp_Bodensatz_konten` | `q1/Queries/Procedures/sp_Bodensatz_konten.sql` | 57 | 0 | 1 |
| `sp_Buchung_GuV_Konto` | `q1/Queries/Procedures/sp_Buchung_GuV_Konto.sql` | 157 | 0 | 0 |
| `sp_Check24_Antrag_Inaktivieren` | `q1/Queries/Procedures/sp_Check24_Antrag_Inaktivieren.sql` | 124 | 1 | 0 |
| `sp_Check24_Antrag_Inaktivieren_Test` | `q1/Queries/Procedures/sp_Check24_Antrag_Inaktivieren_Test.sql` | 124 | 0 | 1 |
| `sp_Check_603_vs_601` | `q1/Queries/Procedures/sp_Check_603_vs_601.sql` | 123 | 0 | 0 |
| `sp_Check_Benchmark_VV_Kunden` | `q1/Queries/Procedures/sp_Check_Benchmark_VV_Kunden.sql` | 113 | 0 | 0 |
| `sp_Check_CRS_Kontoregister` | `q1/Queries/Procedures/sp_Check_CRS_Kontoregister.sql` | 113 | 0 | 0 |
| `SP_Check_Depot_Spesen_Konto` | `q1/Queries/Procedures/SP_Check_Depot_Spesen_Konto.sql` | 202 | 1 | 0 |
| `sp_check_Depots_Bestand` | `q1/Queries/Procedures/sp_Check_Depots_Bestand.sql` | 277 | 0 | 0 |
| `sp_Check_Doppelte_Kest_Tilgung` | `q1/Queries/Procedures/sp_Check_Doppelte_Kest_Tilgung.sql` | 136 | 0 | 0 |
| `sp_Check_Eigenbestand_Lagerstelle` | `q1/Queries/Procedures/sp_Check_Eigenbestand_Lagerstelle.sql` | 143 | 0 | 0 |
| `sp_Check_Formular_Frequenz` | `q1/Queries/Procedures/sp_Check_Formular_Frequenz_WORK.sql` | 287 | 0 | 0 |
| `sp_Check_Jobs` | `q1/Queries/Procedures/sp_Check_Jobs.sql` | 107 | 0 | 0 |
| `sp_Check_KAMA_Lieferungen` | `q1/Queries/Procedures/sp_Check_KAMA_Lieferungen.sql` | 174 | 0 | 0 |
| `sp_Check_Konten_ohne` | `q1/Queries/Procedures/sp_Check_Konten_ohne.sql` | 248 | 0 | 0 |
| `sp_Check_Konten_Zinsgruppe` | `q1/Queries/Procedures/sp_Check_Konten_Zinsgruppe.sql` | 221 | 0 | 0 |
| `sp_Check_Kreditkonten_neu` | `q1/Queries/Procedures/sp_Check_Kreditkonten_neu.sql` | 125 | 0 | 0 |
| `sp_Check_Kunden_Eroeffnungsdatum` | `q1/Queries/Procedures/sp_Check_Kunden_Eroeffnungsdatum.sql` | 179 | 0 | 0 |
| `sp_Check_Kunden_mit` | `q1/Queries/Procedures/sp_Check_Kunden_mit.sql` | 258 | 0 | 0 |
| `sp_Check_Kunden_ohne` | `q1/Queries/Procedures/sp_Check_Kunden_ohne.sql` | 331 | 0 | 0 |
| `sp_Check_Kunden_Team_vs_CRM` | `q1/Queries/Procedures/sp_Check_Kunden_Team_vs_CRM.sql` | 126 | 0 | 0 |
| `sp_Check_KundenProfil` | `q1/Queries/Procedures/sp_Check_KundenProfil.sql` | 169 | 0 | 0 |
| `sp_Check_Kupon_Kest` | `q1/Queries/Procedures/sp_Check_Kupon_Kest.sql` | 167 | 0 | 0 |
| `sp_Check_Kupon_Kest_Onbase` | `q1/Queries/Procedures/sp_Check_Kupon_Kest_Onbase.sql` | 162 | 0 | 0 |
| `sp_Check_LEI_Gueltigkeit` | `q1/Queries/Procedures/sp_Check_LEI_Gueltigkeit.sql` | 131 | 0 | 0 |
| `sp_Check_Mehrfache_Tin` | `q1/Queries/Procedures/sp_Check_Mehrfache_Tin.sql` | 118 | 1 | 0 |
| `sp_Check_MIFIR_Transaktionen` | `q1/Queries/Procedures/sp_Check_MIFIR_Transaktionen.sql` | 186 | 0 | 0 |
| `sp_Check_Portfolio_Reports` | `q1/Queries/Procedures/sp_Check_Portfolio_Reports.sql` | 145 | 0 | 0 |
| `sp_Check_PTP_W10` | `q1/Queries/Procedures/sp_Check_PTP_W10.sql` | 121 | 0 | 0 |
| `sp_Check_Quartalsspesen` | `q1/Queries/Procedures/sp_Check_Quartalsspesen.sql` | 85 | 0 | 2 |
| `sp_Check_Relevante_Person` | `q1/Queries/Procedures/sp_Check_Relevante_Person.sql` | 232 | 0 | 0 |
| `sp_Check_REPP_vs_REKS` | `q1/Queries/Procedures/sp_Check_REPP_vs_REKS.sql` | 233 | 0 | 0 |
| `sp_Check_Risk_Scoring` | `q1/Queries/Procedures/sp_Check_Risk_Scoring.sql` | 210 | 0 | 0 |
| `sp_Check_SFTR_Valuation` | `q1/Queries/Procedures/sp_Check_SFTR_Valuation.sql` | 159 | 0 | 0 |
| `sp_Check_Smart_Invest` | `q1/Queries/Procedures/sp_Check_Smart_Invest.sql` | 342 | 0 | 0 |
| `SP_Check_Spesen_Konto` | `q1/Queries/Procedures/SP_Check_Spesen_Konto.sql` | 231 | 1 | 0 |
| `sp_Check_TIN_Gueltigkeit` | `q1/Queries/Procedures/sp_Check_TIN_Gueltigkeit.sql` | 305 | 0 | 0 |
| `sp_Check_Vermittlerdaten_Controlling` | `q1/Queries/Procedures/sp_Check_Vermittlerdaten_Controlling.sql` | 181 | 0 | 0 |
| `sp_Check_VV_Tipas` | `q1/Queries/Procedures/sp_Check_VV_Tipas.sql` | 176 | 0 | 0 |
| `sp_Check_WP_Art_vs_Depot` | `q1/Queries/Procedures/sp_Check_WP_Art_vs_Depot.sql` | 248 | 0 | 0 |
| `sp_Closed_clients_LMonth` | `q1/Queries/Procedures/sp_Closed_Clients_LMonth.sql` | 115 | 0 | 0 |
| `sp_Create_Ablaufende_Garantien` | `q1/Queries/Procedures/sp_Create_Ablaufende_Garantien.sql` | 123 | 0 | 0 |
| `sp_Create_AML_Art5` | `q1/Queries/Procedures/sp_Create_AML_Art5.sql` | 319 | 0 | 0 |
| `sp_Create_ATIExport_UniCredit` | `q1/Queries/Procedures/SP_Create_ATIExport_UniCredit.sql` | 236 | 0 | 0 |
| `sp_Create_Benutzergruppen_Menuepunkte` | `q1/Queries/Procedures/sp_Create_Benutzergruppen_Menuepunkte.sql` | 135 | 0 | 0 |
| `sp_Create_Best_Execution` | `q1/Queries/Procedures/sp_Create_Best_Execution.sql` | 1435 | 0 | 0 |
| `sp_Create_Check24_Inaktiv_OnBase` | `q1/Queries/Procedures/sp_Create_Check24_Inaktiv_OnBase_test.sql` | 134 | 0 | 0 |
| `sp_Create_Check24_OnBase` | `q1/Queries/Procedures/sp_Create_Check24_OnBase.sql` | 191 | 1 | 0 |
| `sp_Create_Check24_OnBase_Test` | `q1/Queries/Procedures/sp_Create_Check24_OnBase_Test.sql` | 184 | 0 | 1 |
| `sp_Create_CRS_Listen` | `q1/Queries/Procedures/sp_Create_CRS_Listen.sql` | 412 | 0 | 2 |
| `sp_Create_CRS_Meldung_TPAM` | `q1/Queries/Procedures/sp_Create_CRS_Meldung_TPAM.sql` | 183 | 0 | 0 |
| `sp_Create_CRS_Review` | `q1/Queries/Procedures/SP_Create_CRS_Review.sql` | 861 | 1 | 0 |
| `sp_Create_DatenExport_UniCredit` | `q1/Queries/Procedures/SP_Create_DatenExport_UniCredit.sql` | 272 | 0 | 0 |
| `sp_Create_ENR_Balances` | `q1/Queries/Procedures/sp_Create_ENR_Balances.sql` | 367 | 0 | 0 |
| `sp_Create_ENR_Positions` | `q1/Queries/Procedures/sp_Create_ENR_Positions.sql` | 120 | 0 | 0 |
| `sp_Create_Evidenzen_OnBase` | `q1/Queries/Procedures/sp_Create_Evidenzen_OnBase_alt.sql` | 292 | 0 | 0 |
| `sp_Create_FinMgr_Bewegungen` | `q1/Queries/Procedures/sp_Create_FinMgr_Bewegungen.sql` | 622 | 0 | 0 |
| `sp_Create_FinMgr_MasterDaten` | `q1/Queries/Procedures/sp_Create_FinMgr_MasterDaten.sql` | 805 | 0 | 1 |
| `sp_Create_FMG_Plus_Positions` | `q1/Queries/Procedures/sp_Create_FMG_Plus_Positions.sql` | 222 | 0 | 0 |
| `sp_Create_goAML_Transactions` | `q1/Queries/Procedures/sp_Create_goAML_Transactions.sql` | 568 | 0 | 0 |
| `sp_Create_High_Volume_Kunden` | `q1/Queries/Procedures/sp_Create_High_Volume_Kunden_alt.sql` | 660 | 0 | 0 |
| `sp_Create_High_Watermarks` | `q1/Queries/Procedures/sp_Create_High_Watermarks.sql` | 456 | 1 | 0 |
| `sp_Create_High_Watermarks_YtD` | `q1/Queries/Procedures/sp_Create_High_Watermarks_YtD.sql` | 170 | 0 | 1 |
| `sp_Create_Impairment_Test` | `q1/Queries/Procedures/sp_Create_Impairment_Test.sql` | 324 | 0 | 1 |
| `sp_Create_IOMA_Portfolio` | `q1/Queries/Procedures/sp_Create_IOMA_Portfolio.sql` | 222 | 0 | 0 |
| `sp_Create_Kest_Befreiung` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung.sql` | 830 | 1 | 0 |
| `sp_Create_Kest_Befreiung_Test` | `q1/Queries/Procedures/sp_Create_Kest_Befreiung_Test.sql` | 833 | 0 | 1 |
| `sp_Create_Konto_saldo` | `q1/Queries/Procedures/sp_Create_Konto_saldo.sql` | 163 | 0 | 0 |
| `sp_Create_KPMG_Datenabzug` | `q1/Queries/Procedures/sp_Create_KPMG_Datenabzug.sql` | 186 | 0 | 0 |
| `sp_Create_Kredit_Evidenzen_OnBase` | `q1/Queries/Procedures/sp_Create_Kredit_Evidenzen_OnBase.sql` | 319 | 0 | 0 |
| `sp_Create_Kupon_Tilgung` | `q1/Queries/Procedures/sp_Create_Kupon_Tilgung.sql` | 227 | 0 | 0 |
| `sp_Create_Manual_Risk_Review_Test` | `q1/Queries/Procedures/sp_Create_Manual_Risk_Review_Test.sql` | 236 | 0 | 0 |
| `sp_Create_Onbase_Master_Data` | `q1/Queries/Procedures/sp_Create_Onbase_Master_Data.sql` | 245 | 0 | 0 |
| `sp_Create_Portfolio_Valuation` | `q1/Queries/Procedures/sp_Create_Portfolio_Valuation.sql` | 232 | 0 | 0 |
| `sp_Create_QI_UM_Daten` | `q1/Queries/Procedures/sp_Create_QI_UM_Daten.sql` | 212 | 0 | 0 |
| `sp_Create_Raquest_Analyse` | `q1/Queries/Procedures/sp_Create_Raquest_Analyse.sql` | 510 | 0 | 0 |
| `sp_Create_Risk_Review_OnBase` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase.sql` | 170 | 1 | 2 |
| `sp_Create_Risk_Review_OnBase_Test` | `q1/Queries/Procedures/sp_Create_Risk_Review_OnBase_Test.sql` | 429 | 0 | 3 |
| `sp_Create_SRD_2_Interface` | `q1/Queries/Procedures/sp_Create_SRD_2_Interface.sql` | 1018 | 0 | 2 |
| `sp_Create_SRD_2_WP_Trans` | `q1/Queries/Procedures/sp_Create_SRD_2_WP_Trans.sql` | 129 | 1 | 0 |
| `sp_Create_SupportNet_offen` | `q1/Queries/Procedures/sp_Create_SupportNet_offen.sql` | 419 | 1 | 0 |
| `sp_Create_Swiss_Alpine_Balances` | `q1/Queries/Procedures/sp_Create_Swiss_Alpine_Balances.sql` | 200 | 0 | 0 |
| `sp_Create_Table_LaenderStamm` | `q1/Queries/Procedures/sp_Create_Table_LaenderStamm.sql` | 132 | 0 | 0 |
| `sp_Create_Tambas_Assetera_Mapping` | `q1/Queries/Procedures/sp_Create_Tambas_Assetera_Mapping.sql` | 127 | 0 | 0 |
| `sp_Create_TCM_Check_OnBase` | `q1/Queries/Procedures/sp_Create_TCM_Check_OnBase.sql` | 241 | 0 | 0 |
| `sp_Create_Treasury_Listen` | `q1/Queries/Procedures/sp_Create_Treasury_Listen.sql` | 247 | 0 | 0 |
| `sp_Create_Valorlife_Portfolio` | `q1/Queries/Procedures/sp_Create_Valorlife_Portfolio.sql` | 237 | 0 | 0 |
| `sp_Create_Verlustschwellenreport_Meldung` | `q1/Queries/Procedures/sp_Create_Verlustschwellenreport_Meldung.sql` | 155 | 0 | 0 |
| `sp_Create_WHVP_Balances` | `q1/Queries/Procedures/sp_Create_WHVP_Balances.sql` | 129 | 0 | 0 |
| `sp_Create_WHVP_Trades` | `q1/Queries/Procedures/sp_Create_WHVP_Trades.sql` | 154 | 0 | 0 |
| `sp_Create_WP_Trans_Historie` | `q1/Queries/Procedures/sp_Create_WP_Trans_Historie.sql` | 781 | 0 | 0 |
| `sp_Create_WPB_TCM_Clients` | `q1/Queries/Procedures/SP_Create_WPB_TCM_Clients.sql` | 221 | 0 | 0 |
| `sp_Create_ZVK_Eingang_OnBase` | `q1/Queries/Procedures/sp_Create_ZVK_Eingang_OnBase.sql` | 481 | 0 | 3 |
| `sp_Create_ZVK_Master_Data` | `q1/Queries/Procedures/sp_Create_ZVK_Master_Data.sql` | 263 | 1 | 0 |
| `sp_Create_ZVK_Valuta_OnBase` | `q1/Queries/Procedures/sp_Create_ZVK_Valuta_OnBase.sql` | 97 | 1 | 0 |
| `sp_CRS_FATCA_Listen` | `q1/Queries/Procedures/sp_CRS_FATCA_Listen.sql` | 225 | 0 | 0 |
| `sp_Dauerauftraege_Privat` | `q1/Queries/Procedures/sp_Dauerauftraege_Privat.sql` | 391 | 0 | 0 |
| `sp_Devisenhandel_Vontobel` | `q1/Queries/Procedures/sp_Devisenhandel_Vontobel.sql` | 155 | 0 | 0 |
| `sp_Dokumente` | `q1/Queries/Procedures/sp_Dokumente.sql` | 227 | 0 | 0 |
| `sp_email_kundenvolumen_tipas_mailbox` | `q1/Queries/Procedures/sp_email_kundenvolumen_tipas_mailbox.sql` | 115 | 0 | 0 |
| `sp_email_neu_angelegte_anleihen` | `q1/Queries/Procedures/sp_email_neu_angelegte_anleihen.sql` | 106 | 0 | 0 |
| `sp_email_wertpapiere_umbenennen_eng` | `q1/Queries/Procedures/sp_email_wertpapiere_umbenennen_eng.sql` | 67 | 0 | 0 |
| `SP_ErinnerungsMail_Nachbuchen_erlaubt` | `q1/Queries/Procedures/SP_ErinnerungsMail_Nachbuchen_erlaubt.sql` | 64 | 0 | 0 |
| `sp_ESG_Check` | `q1/Queries/Procedures/sp_ESG_Check.sql` | 264 | 0 | 0 |
| `sp_Evidenzen_US_Dokumente` | `q1/Queries/Procedures/sp_Evidenzen_US_Dokumente.sql` | 200 | 0 | 0 |
| `sp_EvidenzVerwaltung` | `q1/Queries/Procedures/sp_EvidenzVerwaltung.sql` | 1008 | 0 | 0 |
| `sp_FATCA_IA_Faellig` | `q1/Queries/Procedures/sp_FATCA_IA_Faellig.sql` | 282 | 0 | 0 |
| `sp_Fatca_Relevanz` | `q1/Queries/Procedures/sp_Fatca_Relevanz.sql` | 150 | 0 | 0 |
| `sp_Fehlerhafte_Corporate_Actions` | `q1/Queries/Procedures/sp_Fehlerhafte_Corporate_Actions.sql` | 158 | 0 | 0 |
| `sp_Fehlerhafte_Quartalsspesen` | `q1/Queries/Procedures/sp_Fehlerhafte_Quartalsspesen.sql` | 182 | 0 | 0 |
| `sp_findtext` | `q1/Queries/Procedures/sp_findtext.sql` | 153 | 0 | 0 |
| `sp_findtext_SP` | `q1/Queries/Procedures/sp_findtext_SP.sql` | 155 | 0 | 0 |
| `sp_Firmen_Ablaufende_Vollmachten` | `q1/Queries/Procedures/sp_Firmen_Ablaufende_Vollmachten.sql` | 220 | 0 | 0 |
| `sp_Firmen_Fehlende_Vollmachten` | `q1/Queries/Procedures/sp_Firmen_Fehlende_Vollmachten.sql` | 189 | 0 | 0 |
| `sp_Firmen_ohne_BO` | `q1/Queries/Procedures/sp_Firmen_ohne_BO.sql` | 140 | 0 | 0 |
| `sp_Firmen_Vollmachten` | `q1/Queries/Procedures/sp_Firmen_Vollmachten.sql` | 160 | 0 | 0 |
| `sp_Formulare_Inaktivieren` | `q1/Queries/Procedures/sp_Formulare_Inaktivieren.sql` | 145 | 0 | 0 |
| `sp_Forwards_Mature` | `q1/Queries/Procedures/sp_Forwards_Mature.sql` | 193 | 0 | 0 |
| `sp_FX_Forwards` | `q1/Queries/Procedures/sp_FX_Forwards.sql` | 272 | 0 | 0 |
| `sp_FX_Kurse_His` | `q1/Queries/Procedures/sp_FX_Kurse_His.sql` | 99 | 0 | 0 |
| `sp_FX_Kurse_Taeglich` | `q1/Queries/Procedures/sp_FX_Kurse_Taeglich.sql` | 186 | 0 | 0 |
| `sp_Geburtstagskinder` | `q1/Queries/Procedures/sp_Geburtstagskinder.sql` | 210 | 0 | 0 |
| `sp_Geldhandel_Check24_OnBase` | `q1/Queries/Procedures/sp_Geldhandel_Check24_OnBase.sql` | 102 | 1 | 0 |
| `sp_Geldhandel_Check24_OnBase_Test` | `q1/Queries/Procedures/sp_Geldhandel_Check24_OnBase_Test.sql` | 100 | 0 | 1 |
| `sp_Gold_Kontrakte` | `q1/Queries/Procedures/sp_Gold_Kontrakte.sql` | 417 | 0 | 0 |
| `sp_Gold_Sparplaene` | `q1/Queries/Procedures/sp_Gold_Sparplaene.sql` | 160 | 0 | 0 |
| `sp_GW_Auswertungen` | `q1/Queries/Procedures/sp_GW_Auswertungen.sql` | 402 | 0 | 0 |
| `sp_InvestorProfile` | `q1/Queries/Procedures/sp_InvestorProfile.sql` | 468 | 0 | 0 |
| `sp_Konten_Gueltigkeit` | `q1/Queries/Procedures/sp_Konten_Gueltigkeit.sql` | 54 | 0 | 0 |
| `sp_Konto_Abgleich_Valantic` | `q1/Queries/Procedures/sp_Konto_Abgleich_Valantic.sql` | 118 | 0 | 0 |
| `sp_Kontoregister_Kontrolle` | `q1/Queries/Procedures/sp_Kontoregister_Kontrolle.sql` | 172 | 0 | 0 |
| `sp_Kredit_Unterschreitungen` | `q1/Queries/Procedures/sp_Kredit_Unterschreitungen.sql` | 245 | 0 | 0 |
| `sp_Kreditkarten_Monatlich` | `q1/Queries/Procedures/sp_Kreditkarten_Monatlich.sql` | 520 | 0 | 0 |
| `sp_Kunden_Cash_Only` | `q1/Queries/Procedures/sp_Kunden_Cash_Only.sql` | 391 | 0 | 0 |
| `sp_Kunden_Check_Compliance` | `q1/Queries/Procedures/sp_Kunden_Check_Compliance.sql` | 269 | 0 | 0 |
| `sp_Kunden_Fluktuation` | `q1/Queries/Procedures/sp_Kunden_Fluktuation.sql` | 312 | 1 | 0 |
| `sp_Kunden_Fluktuation_AdHoc` | `q1/Queries/Procedures/sp_Kunden_Fluktuation_AdHoc.sql` | 188 | 0 | 1 |
| `sp_Kunden_Hochrisiko` | `q1/Queries/Procedures/sp_Kunden_Hochrisiko.sql` | 381 | 0 | 0 |
| `sp_Kunden_Loeschung_DSGVO` | `q1/Queries/Procedures/sp_Kunden_Loeschung_DSGVO.sql` | 234 | 0 | 0 |
| `sp_kunden_ohne_volumen` | `q1/Queries/Procedures/sp_kunden_ohne_volumen.sql` | 77 | 0 | 0 |
| `sp_Kunden_Risikoaenderung` | `q1/Queries/Procedures/sp_Kunden_Risikoaenderung.sql` | 193 | 0 | 0 |
| `sp_Kundenprofil_Depotbestand` | `q1/Queries/Procedures/sp_Kundenprofil_Depotbestand.sql` | 292 | 0 | 0 |
| `sp_Kundensperren_Compliance` | `q1/Queries/Procedures/sp_Kundensperren_Compliance.sql` | 125 | 0 | 0 |
| `sp_Kupon_QI_Abstimmung` | `q1/Queries/Procedures/sp_Kupon_QI_Abstimmung.sql` | 484 | 0 | 0 |
| `sp_Kurscheck_Nostro_Bestand` | `q1/Queries/Procedures/sp_Kurscheck_Nostro_Bestand.sql` | 269 | 0 | 0 |
| `sp_mail_test` | `q1/Queries/Procedures/sp_mail_test.sql` | 67 | 0 | 0 |
| `sp_Mailbox_vs_Spesen` | `q1/Queries/Procedures/sp_Mailbox_vs_Spesen.sql` | 112 | 0 | 0 |
| `sp_Mailing_Gruppen_Kunden` | `q1/Queries/Procedures/sp_Mailing_Gruppen_Kunden.sql` | 337 | 0 | 0 |
| `sp_Mailing_Kunden` | `q1/Queries/Procedures/sp_Mailing_Kunden.sql` | 346 | 0 | 0 |
| `sp_Manuelle_WP_Kurse` | `q1/Queries/Procedures/sp_Manuelle_WP_Kurse.sql` | 159 | 0 | 0 |
| `sp_MIFID_Finanzinstrumente` | `q1/Queries/Procedures/sp_MIFID_Finanzinstrumente.sql` | 167 | 0 | 0 |
| `sp_MIFID_II_BestEx_Offenlegung` | `q1/Queries/Procedures/SP_Mifid_II_BestEx_Offenlegung.sql` | 227 | 0 | 0 |
| `sp_MIFIR_Transaktionen_Onbase` | `q1/Queries/Procedures/sp_MIFIR_Transaktionen_Onbase.sql` | 155 | 0 | 0 |
| `sp_Neue_Wertpapiere` | `q1/Queries/Procedures/sp_Neue_Wertpapiere.sql` | 183 | 0 | 0 |
| `sp_Neue_WPs_Ohne_ISIN` | `q1/Queries/Procedures/sp_Neue_WPs_Ohne_ISIN.sql` | 153 | 0 | 0 |
| `sp_NeuKunden_Sutor` | `q1/Queries/Procedures/sp_NeuKunden_Sutor.sql` | 152 | 0 | 0 |
| `sp_OENB_MELDUNG_RU_BY` | `q1/Queries/Procedures/sp_OENB_MELDUNG_RU_BY.sql` | 189 | 0 | 0 |
| `sp_Offene_Orders` | `q1/Queries/Procedures/sp_Offene_Orders.sql` | 316 | 0 | 0 |
| `sp_Options` | `q1/Queries/Procedures/sp_Options.sql` | 191 | 0 | 0 |
| `sp_Orders_via_Navigator` | `q1/Queries/Procedures/sp_Orders_via_Navigator.sql` | 183 | 0 | 0 |
| `sp_OTC_Dokumente` | `q1/Queries/Procedures/sp_OTC_Dokumente.sql` | 365 | 0 | 0 |
| `sp_Professionelle_Kunden` | `q1/Queries/Procedures/sp_Professionelle_Kunden.sql` | 195 | 0 | 0 |
| `sp_Quest_Auswertung` | `q1/Queries/Procedures/sp_Quest_Auswertung.sql` | 114 | 0 | 0 |
| `sp_Read_Impairment_Daten` | `q1/Queries/Procedures/sp_Read_Impairment_Daten.sql` | 510 | 1 | 0 |
| `sp_Read_Tambas_Daten_FinMgr` | `q1/Queries/Procedures/sp_Read_Tambas_Daten_FinMgr.sql` | 621 | 1 | 0 |
| `sp_Realisierte_Konten` | `q1/Queries/Procedures/sp_Realisierte_Konten.sql` | 122 | 0 | 0 |
| `sp_Review_Nostro_Bestsand_Risk` | `q1/Queries/Procedures/sp_Review_Nostro_Bestsand_Risk.sql` | 207 | 0 | 0 |
| `sp_Risikoklasse_Durchschnitt_VV` | `q1/Queries/Procedures/sp_Risikoklasse_Durchschnitt_VV.sql` | 327 | 0 | 0 |
| `sp_Risk_OENB` | `q1/Queries/Procedures/sp_Risk_OENB.sql` | 403 | 0 | 0 |
| `sp_Risk_Review_Abgeschlossen` | `q1/Queries/Procedures/sp_Risk_Review_Abgeschlossen.sql` | 210 | 0 | 0 |
| `sp_Risk_Review_Check` | `q1/Queries/Procedures/sp_Risk_Review_Check.sql` | 334 | 0 | 0 |
| `sp_Risk_Review_Offen` | `q1/Queries/Procedures/sp_Risk_Review_Offen.sql` | 121 | 0 | 0 |
| `sp_Risk_Review_OnBase_Details` | `q1/Queries/Procedures/sp_Risk_Review_OnBase_Details.sql` | 324 | 0 | 0 |
| `sp_Risk_Review_OnBase_Transaktionen` | `q1/Queries/Procedures/sp_Risk_Review_OnBase_Transaktionen.sql` | 197 | 2 | 0 |
| `sp_RiskScoring_Kontrolle` | `q1/Queries/Procedures/sp_RiskScoring_Kontrolle.sql` | 667 | 0 | 0 |
| `sp_RiskScoring_Onbase` | `q1/Queries/Procedures/sp_RiskScoring_Onbase.sql` | 361 | 2 | 0 |
| `sp_Salden_KO_Sperre_CS` | `q1/Queries/Procedures/sp_Salden_KO_Sperre_CS.sql` | 203 | 0 | 0 |
| `sp_Send_SRD_2_CSV` | `q1/Queries/Procedures/sp_Send_SRD_2_CSV.sql` | 309 | 1 | 0 |
| `sp_sperraenderung` | `q1/Queries/Procedures/sp_Sperraenderungen.sql` | 396 | 0 | 0 |
| `sp_sperrquittierungen` | `q1/Queries/Procedures/sp_sperrquittierungen.sql` | 688 | 1 | 0 |
| `sp_sperrquittierungen_Quartal` | `q1/Queries/Procedures/sp_sperrquittierungen_Quartal.sql` | 224 | 0 | 1 |
| `sp_SupportNet_vs_YouTrack` | `q1/Queries/Procedures/sp_SupportNet_vs_YouTrack.sql` | 144 | 0 | 1 |
| `sp_test` | `q1/Queries/Procedures/a_sp_Test.sql` | 57 | 0 | 0 |
| `sp_Ueberziehungen` | `q1/Queries/Procedures/sp_Ueberziehungen.sql` | 729 | 1 | 0 |
| `sp_Ueberziehungen_Test` | `q1/Queries/Procedures/sp_Ueberziehungen_TEST.sql` | 694 | 0 | 1 |
| `sp_Vollmacht_Sperren` | `q1/Queries/Procedures/sp_Vollmacht_Sperren.sql` | 136 | 0 | 0 |
| `sp_Vollmachten_PEP` | `q1/Queries/Procedures/sp_Vollmachten_PEP.sql` | 197 | 0 | 0 |
| `sp_VST_9999800011_Gegenbuchung` | `q1/Queries/Procedures/sp_VST_9999800011_Gegenbuchung.sql` | 146 | 0 | 0 |
| `sp_VV_Depot_Check` | `q1/Queries/Procedures/sp_VV_Depot_Check.sql` | 165 | 0 | 0 |
| `sp_VV_IP_AenderungAnlage` | `q1/Queries/Procedures/sp_VV_IP_AenderungAnlage.sql` | 281 | 0 | 0 |
| `sp_WP_Bewegungen` | `q1/Queries/Procedures/sp_WP_Bewegungen.sql` | 237 | 0 | 0 |
| `sp_WP_Kontrakte_Taeglich` | `q1/Queries/Procedures/sp_WP_Kontrakte_Taeglich.sql` | 144 | 0 | 0 |
| `sp_WP_Orders` | `q1/Queries/Procedures/sp_WP_Orders.sql` | 497 | 0 | 0 |
| `sp_WP_Trans_Check` | `q1/Queries/Procedures/sp_WP_Trans_Check.sql` | 355 | 0 | 0 |
| `sp_Write_Kunden_MonatsendDaten` | `q1/Queries/Procedures/sp_Write_Kunden_MonatsendDaten.sql` | 508 | 0 | 0 |
| `sp_Write_Kunden_Postfach` | `q1/Queries/Procedures/sp_Write_Kunden_Postfach.sql` | 276 | 0 | 0 |
| `sp_Write_Kunden_Salden` | `q1/Queries/Procedures/sp_Write_Kunden_Salden.sql` | 854 | 0 | 0 |
| `sp_Write_Kunden_Sprache` | `q1/Queries/Procedures/sp_Write_Kunden_Sprache.sql` | 67 | 0 | 0 |
| `sp_Write_Kundenstamm` | `q1/Queries/Procedures/sp_Write_Kundenstamm_Controlling.sql` | 124 | 0 | 0 |
| `sp_Write_Treasury_Salden` | `q1/Queries/Procedures/sp_Write_Treasury_Salden.sql` | 313 | 0 | 0 |
| `sp_ZVK_Ausgaenge_OnBase` | `q1/Queries/Procedures/sp_ZVK_Ausgaenge_OnBase.sql` | 186 | 0 | 0 |
| `sp_ZVK_Compliance` | `q1/Queries/Procedures/sp_ZVK_Compliance.sql` | 320 | 0 | 0 |
| `sp_ZVK_Eingang_Check24` | `q1/Queries/Procedures/sp_ZVK_Eingang_Check24.sql` | 332 | 1 | 0 |
| `sp_ZVK_Eingang_Check24_Test` | `q1/Queries/Procedures/sp_ZVK_Eingang_Check24_Test.sql` | 331 | 0 | 1 |
| `sp_ZVK_Kontrakte` | `q1/Queries/Procedures/sp_ZVK_Kontrakte.sql` | 349 | 1 | 0 |
| `sp_ZVK_Kontrakte_Ford_Verb` | `q1/Queries/Procedures/sp_ZVK_Kontrakte_Ford_Verb.sql` | 234 | 0 | 1 |
| `sp_ZVK_RU_BY_UA` | `q1/Queries/Procedures/sp_ZVK_RU_BY_UA.sql` | 647 | 1 | 0 |
| `sp_ZVK_Sepa_Ausgaenge_OnBase` | `q1/Queries/Procedures/sp_ZVK_Sepa_Ausgaenge_OnBase.sql` | 104 | 0 | 0 |
| `sp_ZVK_Taeglich` | `q1/Queries/Procedures/sp_ZVK_Taeglich.sql` | 770 | 0 | 1 |
| `sp_ZVK_VS_Schwellenwert` | `q1/Queries/Procedures/sp_ZVK_VS_Schwellenwert.sql` | 442 | 1 | 0 |
