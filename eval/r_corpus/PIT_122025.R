rm(list=ls())

df<-read.table("W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/TTC/ttc_lt_PD_monthly1.csv",sep=";", row.names = 1)
df2<-read.table("W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/TTC/ttc_lt_PD1.csv",sep=";", row.names = 1)
colnames(df)<-1:300
pfad<-"W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/PIT"
portPD<-0.0119514 # Portfolio PD (Quelle: ICAAP_GoingConcern_2025-12.xls, Blatt: Kreditrisiko)
scaling<-"VSA" 
id_num<-"1"
outputfile<-paste(pfad,"/PIT_PD_monthly",id_num,".csv",sep="")
outputfiley<-paste(pfad,"/PIT_PD_yearly",id_num,".csv",sep="")
AP<-c(rep(portPD*0.925,18),rep(portPD*0.934,18),rep(portPD*0.937,18))  #FLI Aufschl?ge f?r die n?chsten 3 Jahre manuell bef?llen
#View(AP)
#write.xlsx(AP,file="AP.xlsx")
lt_cPD<-as.matrix(df[,-1])
#View(lt_cPD)
#write.xlsx(lt_cPD,file="lt_cPD.xlsx")
lt_cPD_shift<-cbind(rep(0,nrow(lt_cPD)),lt_cPD[,-ncol(lt_cPD)])
#View(lt_cPD_shift)
#write.xlsx(lt_cPD_shift,file="lt_cPD_shift.xlsx")
marg_PD<-lt_cPD - lt_cPD_shift
#View(marg_PD)
#write.xlsx(marg_PD,file="marg_PD.xlsx")
lt_conPD<-marg_PD/(1 - lt_cPD_shift)
#View(lt_conPD)
#write.xlsx(lt_conPD,file="lt_conPD.xlsx")
if(scaling=="VSA"){
  VSA<-AP/portPD
  #View(VSA)
  #write.xlsx(VSA,file="VSA.xlsx")
  }
lt_conPD_PIT<-NULL
fakt<-data.frame(rep(portPD,length(AP)),AP)
for(j in 1:nrow(fakt)){
  if(scaling=="Bayesian"){
    lt_conPD_PIT<-cbind(lt_conPD_PIT,(bayes(fakt[j,1],fakt[j,2])*lt_conPD[,j])/(bayes(fakt[j,2],fakt[j,1])*(1- lt_conPD[,j])+bayes(fakt[j,1],fakt[j,2])*lt_conPD[,j]))
   }else{
   lt_conPD_PIT<-cbind(lt_conPD_PIT,VSA[j]*lt_conPD[,j])
   #write.xlsx(lt_conPD_PIT,file="lt_conPD_PIT.xlsx")
   }
  }
lt_cPD_PIT<-lt_cPD
lt_cPD_PIT[,1]<-lt_conPD_PIT[,1]
for(j in 2:ncol(lt_conPD_PIT)){
  lt_cPD_PIT[,j]<-lt_cPD_PIT[,j -1]+(1- lt_cPD_PIT[,j -1])*lt_conPD_PIT[,j]
 # write.xlsx(lt_cPD_PIT,file="lt_cPD_PIT.xlsx")
  }
for(j in (ncol(lt_conPD_PIT)+1):ncol(lt_cPD_PIT)){
  lt_cPD_PIT[,j]<-lt_cPD_PIT[,j -1]+lt_conPD[,j]*(1- lt_cPD_PIT[,j -1])
  }
lt_cPD_PIT_monthly<-cbind(df[,1],lt_cPD_PIT)
#write.xlsx(lt_cPD_PIT_monthly,file="lt_cPD_PIT_monthly.xlsx")
lt_cPD_PIT_yearly<-cbind(df[,1],lt_cPD_PIT[,seq(12,ncol(lt_cPD_PIT),by=12)])
write.table(lt_cPD_PIT_monthly,outputfile,sep = ";",row.names = F,col.names = F)
print(lt_cPD_PIT_monthly)
write.table(lt_cPD_PIT_yearly,outputfiley,sep = ";",row.names = F,col.names = F)


#plot marginal PDs
# f?r die Berechnung werden monatliche Daten verwendet, f?r die graphische Darstellung jedoch j?hrliche

colnames(df2)<-1:26
lt_cPD2<-as.matrix(df2[,-1])
lt_cPD_shift2<-cbind(rep(0,nrow(lt_cPD2)),lt_cPD2[,-ncol(lt_cPD2)])
marg_PD2<-lt_cPD2 - lt_cPD_shift2


plot(marg_PD2[18,],col="White")
lines(marg_PD2[1,])
lines(marg_PD2[2,])
lines(marg_PD2[3,])
lines(marg_PD2[4,])
lines(marg_PD2[5,])
lines(marg_PD2[6,])
lines(marg_PD2[7,])
lines(marg_PD2[8,])
lines(marg_PD2[9,])
lines(marg_PD2[10,])
lines(marg_PD2[11,])
lines(marg_PD2[12,])
lines(marg_PD2[13,])
lines(marg_PD2[14,])
lines(marg_PD2[15,])
lines(marg_PD2[16,])
lines(marg_PD2[17,])
lines(marg_PD2[18,])

