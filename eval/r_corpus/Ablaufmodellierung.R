# define functions for Stochastic Tool

# probFirstHittingTime2
probFirstHittingTime2 = function(alpha, sig, mu, t) {
  lambda = mu-0.5*sig^2
  prob = alpha^(2*lambda/sig^2)*pnorm((log(alpha)+lambda*t)/(sig*sqrt(t)))+pnorm((log(alpha)-lambda*t)/(sig*sqrt(t)))
  return(prob)
}

# rtbis
rtbis = function(alpha, sig, mu, t) {
  lambda = mu+0.5*sig^2
  
  cap = 1
  floor = 0
  precision = 0.000001
  
  rtbis = mean(c(cap, floor))
  value = probFirstHittingTime2(rtbis, sig, lambda, t)
  
  while (abs(value - (1-alpha)) > precision) {
    if ((value - (1-alpha)) < precision) {
      floor = rtbis
    } else {
      cap = rtbis
    }
    rtbis = mean(c(cap, floor))
    value = probFirstHittingTime2(rtbis, sig, lambda, t)
  }
  return(rtbis)
}


# Ablaufmodellierung Parametrisch
Ablaufmodellierung_GBM = function(Volumen, Bodensatz, Konfidenz, stand.abw, drift, TimeBuckets) {
  Abbaubestand = Volumen - Bodensatz
  TimeBuckets2 = append(TimeBuckets+1, 1, 0)[-length(TimeBuckets)-1]
  
  Vector1 = c(1:tail(TimeBuckets,1))/12
  dim(Vector1) = c(1,tail(TimeBuckets,1))
  Vector1 = apply(X = Vector1, MARGIN = 2, FUN = rtbis, alpha = Konfidenz, sig = stand.abw, mu = drift)
  Vector1 = append(Vector1, 1, after = 0)
  Vector1 = cumsum(-diff(Vector1) * Volumen)
  Vector1[Vector1 > Abbaubestand] = Abbaubestand
  Vector1 = append(Vector1, 0, after = 0)
  Vector1 = diff(Vector1)
  
  Vector2 = rep(NA, length(TimeBuckets))
  for (i in 1:length(TimeBuckets)) {
    Vector2[i] = mean(Vector1[TimeBuckets2[i]:TimeBuckets[i]])
  }
  
  Vector3 = Vector2[2:length(TimeBuckets)] * TimeBuckets[1:length(TimeBuckets)-1]
  Vector3 = append(Vector3, 0, after = length(TimeBuckets)-1)
  
  AbflussAbsolut = Vector2 * TimeBuckets - Vector3
  AbflussAbsolut = append(AbflussAbsolut, Volumen - sum(AbflussAbsolut), after = length(TimeBuckets))
  AbflussRelativ = AbflussAbsolut / Volumen
  
  LAB = rbind(AbflussAbsolut, AbflussRelativ)
  colnames(LAB) = c(TimeBuckets, "Bodensatz")
  row.names(LAB) = c("Abfluss in WHG", "Abfluss in %")
  
  return(AbflussRelativ)
}


# Ablaufmodellierung Monte Carlo
Ablaufmodellierung_BS = function(Verteilungsdaten, Volumen, Bodensatz, Konfidenz, n, TimeBuckets) {
  
  Abbaubestand = Volumen - Bodensatz
  TimeBuckets2 = append(TimeBuckets+1, 1, 0)[-length(TimeBuckets)-1]
  StopKriterium = -1
  
  Renditen_roh = diff(log(Verteilungsdaten))
  Drift = mean(Renditen_roh)
  Renditen = Renditen_roh - Drift
  
  while (StopKriterium < 0) {
    
    SimulationsMatrix = replicate(n, cumsum(sample(Renditen, tail(TimeBuckets,1), replace = TRUE)))
    Vector1 = exp(cummin(apply(SimulationsMatrix, 1, quantile, probs = 1 - Konfidenz)))
    Vector1 = append(Vector1, 1, after = 0)
    Vector1 = cumsum(-diff(Vector1) * Volumen)
    Vector1[Vector1 > Abbaubestand] = Abbaubestand
    Vector1 = append(Vector1, 0, after = 0)
    Vector1 = diff(Vector1)
    
    Vector2 = rep(NA, length(TimeBuckets))
    for (i in 1:length(TimeBuckets)) {
      Vector2[i] = mean(Vector1[TimeBuckets2[i]:TimeBuckets[i]])
    }
    
    Vector3 = Vector2[2:length(TimeBuckets)] * TimeBuckets[1:length(TimeBuckets)-1]
    Vector3 = append(Vector3, 0, after = length(TimeBuckets)-1)
    
    AbflussAbsolut = Vector2 * TimeBuckets - Vector3
    AbflussAbsolut = append(AbflussAbsolut, Volumen - sum(AbflussAbsolut), after = length(TimeBuckets))
    
    StopKriterium = min(AbflussAbsolut)
  }
  
  AbflussRelativ = AbflussAbsolut / Volumen
  
  LAB = rbind(AbflussAbsolut, AbflussRelativ)
  colnames(LAB) = c(TimeBuckets, "Bodensatz")
  row.names(LAB) = c("Abfluss in WHG", "Abfluss in %")
  
  return(AbflussRelativ)
}

# StochastikTool
StochastikTool = function(LAB5) {
  # Lade ben?tigte Formeln und Packages
  
  #source("LAB_V2.R")
  library("xlsx")
  
  # Dateninput
  # Achtung! im CSV Punkt statt Komma verwenden, ansonsten Fehler
  
  TimeBuckets = c(1,2,3,6,12,24,36,48,60,120)
  InputMatrix = matrix(nrow = 5, ncol = 15)
  colnames(InputMatrix) = colnames(LAB5[1:15])
  rownames(InputMatrix) = c("Wachstum", "Standardabweichung", "Bodensatz relativ", "Bodensatz absolut", "Stichtagsbestand")
  
  for (i in 1:15) {
    InputMatrix[1,i] = mean(diff(log(LAB5[,i])))*12
    InputMatrix[2,i] = sd(diff(log(LAB5[,i])))*sqrt(12)
    InputMatrix[3,i] = min(1+exp(min(diff(log(LAB5[,i]))))-1, min(LAB5[,i]) / LAB5[nrow(LAB5),i],0.45)
    InputMatrix[4,i] = InputMatrix[3,i] * LAB5[nrow(LAB5),i]
    InputMatrix[5,i] = LAB5[nrow(LAB5),i]
  }
  
  InputMatrix = as.data.frame(InputMatrix)
  
  # Konfidenzniveaus
  Normal = 0.95
  Bankkrise = 0.99
  Marktkrise = 0.975
  KombinierteKrise = 0.999
  
  # Berechnung Abl?ufe
  N_EUR_Firma = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Firma, tail(LAB5$EUR_tgl_flg_Firma, 1), InputMatrix$EUR_tgl_flg_Firma[4], Normal, 10000, TimeBuckets)
  N_EUR_KMU = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_KMU, tail(LAB5$EUR_tgl_flg_KMU, 1), InputMatrix$EUR_tgl_flg_KMU[4], Normal, 10000, TimeBuckets)
  N_EUR_Privat = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Privat, tail(LAB5$EUR_tgl_flg_Privat, 1), InputMatrix$EUR_tgl_flg_Privat[4], Normal, 10000, TimeBuckets)
  N_USD_Firma = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Firma, tail(LAB5$USD_tgl_flg_Firma, 1), InputMatrix$USD_tgl_flg_Firma[4], Normal, 10000, TimeBuckets)
  N_USD_KMU = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_KMU, tail(LAB5$USD_tgl_flg_KMU, 1), InputMatrix$USD_tgl_flg_KMU[4], Normal, 10000, TimeBuckets)
  N_USD_Privat = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Privat, tail(LAB5$USD_tgl_flg_Privat, 1), InputMatrix$USD_tgl_flg_Privat[4], Normal, 10000, TimeBuckets)
  N_GBP_Firma = Ablaufmodellierung_BS(LAB5$GBP_tgl_flg_Firma, tail(LAB5$GBP_tgl_flg_Firma, 1), InputMatrix$GBP_tgl_flg_Firma[4], Normal, 10000, TimeBuckets)
  N_GBP_KMU = Ablaufmodellierung_BS(LAB5$GBP_tgl_flg_KMU, tail(LAB5$GBP_tgl_flg_KMU, 1), InputMatrix$GBP_tgl_flg_KMU[4], Normal, 10000, TimeBuckets)
  N_GBP_Privat = Ablaufmodellierung_BS(LAB5$GBP_tgl_flg_Privat, tail(LAB5$GBP_tgl_flg_Privat, 1), InputMatrix$GBP_tgl_flg_Privat[4], Normal, 10000, TimeBuckets)
  N_AUD_Firma = Ablaufmodellierung_BS(LAB5$AUD_tgl_flg_Firma, tail(LAB5$AUD_tgl_flg_Firma, 1), InputMatrix$AUD_tgl_flg_Firma[4], Normal, 10000, TimeBuckets)
  N_AUD_KMU = Ablaufmodellierung_BS(LAB5$AUD_tgl_flg_KMU, tail(LAB5$AUD_tgl_flg_KMU, 1), InputMatrix$AUD_tgl_flg_KMU[4], Normal, 10000, TimeBuckets)
  N_AUD_Privat = Ablaufmodellierung_BS(LAB5$AUD_tgl_flg_Privat, tail(LAB5$AUD_tgl_flg_Privat, 1), InputMatrix$AUD_tgl_flg_Privat[4], Normal, 10000, TimeBuckets)
  N_CAD_Firma = Ablaufmodellierung_BS(LAB5$CAD_tgl_flg_Firma, tail(LAB5$CAD_tgl_flg_Firma, 1), InputMatrix$CAD_tgl_flg_Firma[4], Normal, 10000, TimeBuckets)
  N_CAD_KMU = Ablaufmodellierung_BS(LAB5$CAD_tgl_flg_KMU, tail(LAB5$CAD_tgl_flg_KMU, 1), InputMatrix$CAD_tgl_flg_KMU[4], Normal, 10000, TimeBuckets)
  N_CAD_Privat = Ablaufmodellierung_BS(LAB5$CAD_tgl_flg_Privat, tail(LAB5$CAD_tgl_flg_Privat, 1), InputMatrix$CAD_tgl_flg_Privat[4], Normal, 10000, TimeBuckets)
  
  M_EUR_Firma = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Firma, tail(LAB5$EUR_tgl_flg_Firma, 1), InputMatrix$EUR_tgl_flg_Firma[4], Marktkrise, 10000, TimeBuckets)
  M_EUR_KMU = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_KMU, tail(LAB5$EUR_tgl_flg_KMU, 1), InputMatrix$EUR_tgl_flg_KMU[4], Marktkrise, 10000, TimeBuckets)
  M_EUR_Privat = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Privat, tail(LAB5$EUR_tgl_flg_Privat, 1), InputMatrix$EUR_tgl_flg_Privat[4], Marktkrise, 10000, TimeBuckets)
  M_USD_Firma = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Firma, tail(LAB5$USD_tgl_flg_Firma, 1), InputMatrix$USD_tgl_flg_Firma[4], Marktkrise, 10000, TimeBuckets)
  M_USD_KMU = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_KMU, tail(LAB5$USD_tgl_flg_KMU, 1), InputMatrix$USD_tgl_flg_KMU[4], Marktkrise, 10000, TimeBuckets)
  M_USD_Privat = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Privat, tail(LAB5$USD_tgl_flg_Privat, 1), InputMatrix$USD_tgl_flg_Privat[4], Marktkrise, 10000, TimeBuckets)
  
  B_EUR_Firma = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Firma, tail(LAB5$EUR_tgl_flg_Firma, 1), InputMatrix$EUR_tgl_flg_Firma[4], Bankkrise, 10000, TimeBuckets)
  B_EUR_KMU = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_KMU, tail(LAB5$EUR_tgl_flg_KMU, 1), InputMatrix$EUR_tgl_flg_KMU[4], Bankkrise, 10000, TimeBuckets)
  B_EUR_Privat = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Privat, tail(LAB5$EUR_tgl_flg_Privat, 1), InputMatrix$EUR_tgl_flg_Privat[4], Bankkrise, 10000, TimeBuckets)
  B_USD_Firma = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Firma, tail(LAB5$USD_tgl_flg_Firma, 1), InputMatrix$USD_tgl_flg_Firma[4], Bankkrise, 10000, TimeBuckets)
  B_USD_KMU = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_KMU, tail(LAB5$USD_tgl_flg_KMU, 1), InputMatrix$USD_tgl_flg_KMU[4], Bankkrise, 10000, TimeBuckets)
  B_USD_Privat = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Privat, tail(LAB5$USD_tgl_flg_Privat, 1), InputMatrix$USD_tgl_flg_Privat[4], Bankkrise, 10000, TimeBuckets)
  
  K_EUR_Firma = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Firma, tail(LAB5$EUR_tgl_flg_Firma, 1), InputMatrix$EUR_tgl_flg_Firma[4], KombinierteKrise, 10000, TimeBuckets)
  K_EUR_KMU = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_KMU, tail(LAB5$EUR_tgl_flg_KMU, 1), InputMatrix$EUR_tgl_flg_KMU[4], KombinierteKrise, 10000, TimeBuckets)
  K_EUR_Privat = Ablaufmodellierung_BS(LAB5$EUR_tgl_flg_Privat, tail(LAB5$EUR_tgl_flg_Privat, 1), InputMatrix$EUR_tgl_flg_Privat[4], KombinierteKrise, 10000, TimeBuckets)
  K_USD_Firma = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Firma, tail(LAB5$USD_tgl_flg_Firma, 1), InputMatrix$USD_tgl_flg_Firma[4], KombinierteKrise, 10000, TimeBuckets)
  K_USD_KMU = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_KMU, tail(LAB5$USD_tgl_flg_KMU, 1), InputMatrix$USD_tgl_flg_KMU[4], KombinierteKrise, 10000, TimeBuckets)
  K_USD_Privat = Ablaufmodellierung_BS(LAB5$USD_tgl_flg_Privat, tail(LAB5$USD_tgl_flg_Privat, 1), InputMatrix$USD_tgl_flg_Privat[4], KombinierteKrise, 10000, TimeBuckets)
  
  
  
  
  LAB_Normal = rbind(N_EUR_Firma, N_EUR_KMU, N_EUR_Privat, N_USD_Firma, N_USD_KMU, N_USD_Privat, N_GBP_Firma, N_GBP_KMU, N_GBP_Privat, N_AUD_Firma, N_AUD_KMU, N_AUD_Privat, N_CAD_Firma, N_CAD_KMU, N_CAD_Privat)
  colnames(LAB_Normal) = c(1,2,3,6,12,24,36,48,60,120,"Bodensatz")
  
  LAB_Markt = rbind(M_EUR_Firma, M_EUR_KMU, M_EUR_Privat, M_USD_Firma, M_USD_KMU, M_USD_Privat)
  colnames(LAB_Markt) = c(1,2,3,6,12,24,36,48,60,120,"Bodensatz")
  
  LAB_Bank = rbind(B_EUR_Firma, B_EUR_KMU, B_EUR_Privat, B_USD_Firma, B_USD_KMU, B_USD_Privat)
  colnames(LAB_Bank) = c(1,2,3,6,12,24,36,48,60,120,"Bodensatz")
  
  LAB_Kombi = rbind(K_EUR_Firma, K_EUR_KMU, K_EUR_Privat, K_USD_Firma, K_USD_KMU, K_USD_Privat)
  colnames(LAB_Kombi) = c(1,2,3,6,12,24,36,48,60,120,"Bodensatz")
  
  write.xlsx(LAB_Normal, file = "Ablaufmodellierung.xlsx", sheetName = "Normalfall", col.names = TRUE, row.names = TRUE, append = FALSE)
  write.xlsx(LAB_Markt, file = "Ablaufmodellierung.xlsx", sheetName = "Marktkrise", col.names = TRUE, row.names = TRUE, append = TRUE)
  write.xlsx(LAB_Bank, file = "Ablaufmodellierung.xlsx", sheetName = "Bankkrise", col.names = TRUE, row.names = TRUE, append = TRUE)
  write.xlsx(LAB_Kombi, file = "Ablaufmodellierung.xlsx", sheetName = "Kombinierte Krise", col.names = TRUE, row.names = TRUE, append = TRUE)
  
  print("Berechnung abgeschlossen")
}
