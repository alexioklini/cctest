#Installation der notwendigen Pakete - Achtung wenn bereits einmal installiert dann nur mehr laden mit library

install.packages("dplyr") #Datenanalyse
install.packages("ggplot2") #Visualisierung Datenergebnisse
install.packages("tidyverse") #Visualisierung Datenergebnisse
install.packages("ggpubr")
install.packages("KSgeneral") #Package für KS Test
install.packages("tseries") #Package für JB Test
install.packages("nortest") #Package für SF Test
install.packages("devtools") #Package für Datenergebnisse
install.packages("tibble") #Package für Spaltenergänzung

#Laden der Pakete
library("dplyr")
library("ggplot2")
library("tidyverse")
library("ggpubr")
library("KSgeneral")
library("tseries")
library("nortest")
library("devtools")
library("tibble")
library("readxl")

#1_________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig Firma - Achtung in Console einzugeben
EUR_Firma_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_Firma_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_Firma_NEU <- as.numeric(unlist(EUR_Firma_Daten))

#Historischer Minimal- und Maximalwert
min(EUR_Firma_NEU)
max(EUR_Firma_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

EUR_Firma_tibble <- as_tibble(EUR_Firma_NEU)

Datum_EUR_Firma <- EUR_Firma_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_Firma_tibble))
)

ggplot(data=Datum_EUR_Firma,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size=9, angle=90, hjust = 0.5))


#2_________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig KMU
EUR_KMU_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_KMU_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_KMU_NEU <- as.numeric(unlist(EUR_KMU_Daten))

#Historischer Minimal- und Maximalwert
min(EUR_KMU_NEU)
max(EUR_KMU_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

EUR_KMU_tibble <- as_tibble(EUR_KMU_NEU)

Datum_EUR_KMU <- EUR_KMU_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_KMU_tibble))
)

ggplot(data=Datum_EUR_KMU,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size = 9, angle=90,hjust = 1))


#3_________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig Privat
EUR_Privat_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_Privat_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_Privat_NEU <- as.numeric(unlist(EUR_Privat_Daten))

#Historischer Minimal- und Maximalwert
min(EUR_Privat_NEU)
max(EUR_Privat_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

EUR_Privat_tibble <- as_tibble(EUR_Privat_NEU)

Datum_EUR_Privat <- EUR_Privat_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_KMU_tibble))
)

ggplot(data=Datum_EUR_Privat,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size=9, angle=90,hjust = 1))

hist(EUR_Privat_tibble$value,
     main ="Histogramm der Variable")

plot(density(EUR_Privat_tibble$value))


#4_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig Firma
USD_Firma_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_Firma_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_Firma_NEU <- as.numeric(unlist(USD_Firma_Daten))

#Historischer Minimal- und Maximalwert
min(USD_Firma_NEU)
max(USD_Firma_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

USD_Firma_tibble <- as_tibble(USD_Firma_NEU)

Datum_USD_Firma <- USD_Firma_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_Firma_tibble))
)

ggplot(data=Datum_USD_Firma,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size=9, angle=90,hjust = 1))


#5_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig KMU
USD_KMU_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_KMU_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_KMU_NEU <- as.numeric(unlist(USD_KMU_Daten))

#Historischer Minimal- und Maximalwert
min(USD_KMU_NEU)
max(USD_KMU_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

USD_KMU_tibble <- as_tibble(USD_KMU_NEU)

Datum_USD_KMU <- USD_KMU_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_KMU_tibble))
)

ggplot(data=Datum_USD_KMU,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size=9, angle=90,hjust = 1))



#6_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig Privat
USD_Privat_Daten <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_Privat_Daten.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_Privat_NEU <- as.numeric(unlist(USD_Privat_Daten))

#Historischer Minimal- und Maximalwert
min(USD_Privat_NEU)
max(USD_Privat_NEU)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

USD_Privat_tibble <- as_tibble(USD_Privat_NEU)

Datum_USD_Privat <- USD_Privat_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_Privat_tibble))
)

ggplot(data=Datum_USD_Privat,mapping = aes(x=Datum,y=value))+
  geom_line(linewidth = 0.002, colour="gray")+
  geom_area(fill="cadetblue4",alpha=0.5)+
  labs(x="Datum",y="Bestandshöhe")+
  scale_x_date(date_breaks = "1 year", date_labels = "%Y")+
  scale_y_continuous(labels = scales::comma)+
  theme_minimal()+
  theme(axis.text.x = element_text(size=9, angle=90,hjust = 1))


#2.1 - Test mit Logarithmus _________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig Firma
EUR_Firma_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_Firma_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_Firma_LOG_NEU <- as.numeric(unlist(EUR_Firma_Daten_LOG))

#Statistik Kennzahlen
sd(EUR_Firma_LOG_NEU)
mean(EUR_Firma_LOG_NEU)

#Maximale Veränderung 
max(EUR_Firma_LOG_NEU)

#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(EUR_Firma_LOG_NEU,"pnorm",mean=mean(EUR_Firma_LOG_NEU),sd=sd(EUR_Firma_LOG_NEU))
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(EUR_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(EUR_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(EUR_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(EUR_Firma_LOG_NEU, pch=1, main= "QQ Plot - EUR Firma LOG")
qqline(EUR_Firma_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

EUR_Firma_LOG_tibble <- as_tibble(EUR_Firma_LOG_NEU)

Datum_EUR_LOG_Firma <- EUR_Firma_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_Firma_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(EUR_Firma_LOG_tibble$value,
     main ="Histogramm - EUR Firma LOG")

#Dichteverteilung
plot(density(EUR_Firma_LOG_tibble$value),main = "Dichteverteilung - EUR Firma LOG")

##Ergebnis: keine Normalverteilung


#2.2 - Test mit Logarithmus_________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig KMU
EUR_KMU_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_KMU_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_KMU_LOG_NEU <- as.numeric(unlist(EUR_KMU_Daten_LOG))

#Statistik Kennzahlen
sd(EUR_KMU_LOG_NEU)
mean(EUR_KMU_LOG_NEU)


#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(EUR_KMU_LOG_NEU,"pnorm",mean=mean(EUR_KMU_LOG_NEU),sd=sd(EUR_KMU_LOG_NEU))
#Ergebnis: Verwerfen der Nullhypothese --> Normalverteilung; P-Wert größer als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(EUR_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(EUR_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(EUR_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(EUR_KMU_LOG_NEU, pch=1, main= "Normalverteilung - EUR KMU tägl. fällig")
qqline(EUR_KMU_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung - Bestandsveränderung

EUR_KMU_LOG_tibble <- as_tibble(EUR_KMU_LOG_NEU)

Datum_KMU_LOG_Firma <- EUR_KMU_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_KMU_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(EUR_KMU_LOG_tibble$value,
     main ="Histogramm - EUR KMU LOG")

#Dichteverteilung
plot(density(EUR_KMU_LOG_tibble$value),main = "Dichteverteilung - EUR KMU LOG")


##Ergebnis: keine Normalverteilung


#2.3 - Test mit Logarithmus_________________________________________________________________________________
#LOAD DATA - EUR tägl. fällig Privat
EUR_Privat_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/EUR_Privat_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
EUR_Privat_LOG_NEU <- as.numeric(unlist(EUR_Privat_Daten_LOG))

#Statistik Kennzahlen
sd(EUR_Privat_LOG_NEU)
mean(EUR_Privat_LOG_NEU)

#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(EUR_Privat_LOG_NEU,"pnorm",mean=mean(EUR_Privat_LOG_NEU),sd=sd(EUR_Privat_LOG_NEU))
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(EUR_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(EUR_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(EUR_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert größer als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(EUR_Privat_LOG_NEU, pch=1, main= "Normalverteilung - EUR Privat tägl. fällig")
qqline(EUR_Privat_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung

EUR_Privat_LOG_tibble <- as_tibble(EUR_Privat_LOG_NEU)

Datum_Privat_LOG_Firma <- EUR_Privat_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(EUR_Privat_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(EUR_Privat_LOG_tibble$value,
     main ="Histogramm - EUR Privat LOG")

#Dichteverteilung
plot(density(EUR_Privat_LOG_tibble$value),main = "Dichteverteilung - EUR Privat LOG")


##Ergebnis: keine Normalverteilung



#2.4 - Test mit Logarithmus_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig Firma
USD_Firma_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_Firma_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_Firma_LOG_NEU <- as.numeric(unlist(USD_Firma_Daten_LOG))

#Statistik Kennzahlen
sd(USD_Firma_LOG_NEU)
mean(USD_Firma_LOG_NEU)


#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(USD_Firma_LOG_NEU,"pnorm",mean=mean(USD_Firma_LOG_NEU),sd=sd(USD_Firma_LOG_NEU))
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(USD_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(USD_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(USD_Firma_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(USD_Firma_LOG_NEU, pch=1, main= "Normalverteilung - USD Firma tägl. fällig")
qqline(USD_Firma_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung

USD_Firma_LOG_tibble <- as_tibble(USD_Firma_LOG_NEU)

Datum_USD_LOG_Firma <- USD_Firma_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_Firma_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(USD_Firma_LOG_tibble$value,
     main ="Histogramm - USD Firma LOG")

#Dichteverteilung
plot(density(USD_Firma_LOG_tibble$value),main = "Dichteverteilung - USD Firma LOG")


##Ergebnis: keine Normalverteilung





#2.5 - Test mit Logarithmus_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig KMU
USD_KMU_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_KMU_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_KMU_LOG_NEU <- as.numeric(unlist(USD_KMU_Daten_LOG))

#Statistik Kennzahlen
sd(USD_KMU_LOG_NEU)
mean(USD_KMU_LOG_NEU)


#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(USD_KMU_LOG_NEU,"pnorm",mean=mean(USD_KMU_LOG_NEU),sd=sd(USD_KMU_LOG_NEU))
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(USD_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(USD_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(USD_KMU_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(USD_KMU_LOG_NEU, pch=1, main= "Normalverteilung - USD KMU tägl. fällig")
qqline(USD_KMU_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung

USD_KMU_LOG_tibble <- as_tibble(USD_KMU_LOG_NEU)

Datum_USD_LOG_KMU <- USD_KMU_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_KMU_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(USD_KMU_LOG_tibble$value,
     main ="Histogramm - USD KMU LOG")

#Dichteverteilung
plot(density(USD_KMU_LOG_tibble$value),main = "Dichteverteilung - USD KMU LOG")


##Ergebnis: keine Normalverteilung






#2.6 - Test mit Logarithmus_________________________________________________________________________________
#LOAD DATA - USD tägl. fällig Privat
USD_Privat_Daten_LOG <- read_excel("R:/Risikomanagement/Liquidität/Bodensatz/Aktualisierung Bodensätze für 2026/Datenbasis/TEST R/USD_Privat_Daten_LOG.xlsx")

#Aufbereitung in notwendige Datenformatierung
USD_Privat_LOG_NEU <- as.numeric(unlist(USD_Privat_Daten_LOG))

#Statistik Kennzahlen
sd(USD_Privat_LOG_NEU)
mean(USD_Privat_LOG_NEU)


#TESTEN DER NORMALVERTEILUNG
#Kolmogorov-Smirnov Test
ks.test(USD_Privat_LOG_NEU,"pnorm",mean=mean(USD_Privat_LOG_NEU),sd=sd(USD_Privat_LOG_NEU))
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert größer als Signifikanzniveau 

#Jarque-Bera Test
jarque.bera.test(USD_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau 

#Shapiro-Francia Test 
sf.test(USD_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Alternative zu SF Test --> Shapiro Wilk Test
shapiro.test(USD_Privat_LOG_NEU)
#Ergebnis: Annahme der Nullhypothese --> keine Normalverteilung; P-Wert kleiner als Signifikanzniveau

#Darstellung Normalverteilung vs. Verteilung Datensatz 
qqnorm(USD_Privat_LOG_NEU, pch=1, main= "Normalverteilung - USD Privat tägl. fällig")
qqline(USD_Privat_LOG_NEU, col="blue", lwd = 2)

#Aufbereitung für grafische Darstellung

USD_Privat_LOG_tibble <- as_tibble(USD_Privat_LOG_NEU)

Datum_USD_LOG_Privat <- USD_Privat_LOG_tibble %>% add_column(
  Datum = seq(as.Date("2013-01-01"),by="month",length.out=nrow(USD_Privat_LOG_tibble))
)

#Double Check Normalverteilung

#Histogramm
hist(USD_Privat_LOG_tibble$value,
     main ="Histogramm - USD Privat LOG")

#Dichteverteilung
plot(density(USD_Privat_LOG_tibble$value),main = "Dichteverteilung - USD Privat LOG")


##Ergebnis: keine Normalverteilung










