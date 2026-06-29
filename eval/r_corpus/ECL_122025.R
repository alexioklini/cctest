rm(list=ls())

pfad<-"W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung"

source(paste(pfad,"/R codes/ECL_Hilfsfunktion_2.R",sep=""))

df<-read.csv2("W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/TL/deals_TL2026_Konzern.csv",sep=";",header=T,stringsAsFactors = F,dec=",")

df<-df[!apply(is.na(df) | df == "", 1, all),]

pdtime<-"PD_input_monthly"
interpol<-"First_order"
External_mode<-1

M_cpd<-readcsv2(paste(pfad,"/output files/122025/PIT/PIT_PD_monthly1.csv",sep=""),convmat=TRUE,readtime = 1)
print(M_cpd)
sys_t<-format(Sys.time(),"%Y_%m_%d_%H_%M_%S")

outputfile1<-paste(pfad,"/output files/122025/ECL/ECL_deals_Konzern",sys_t,".csv",sep="")



##########################################################


#Start processing the loaded data

#set remaining maturity as integers

df$Month_Remaining<-as.integer(df$Month_Remaining)

#Store PD-curves in array
PD_cube <- array(rep(1, nrow(M_cpd)*ncol(M_cpd)), dim=c(nrow(M_cpd),ncol(M_cpd),1))
PD_cube[ , ,1]=M_cpd

colnames(df)[which(colnames(df)=="rstage")]<-"Bucket"

#Calculate 12 month ECL
df["YM_ECL"] <- mapply(get_12M_ECL,df$Month_Remaining,df$Rating,df$Exposure,df$LGD,df$EIR,df$Amortization,1)

#Calculate lifetime ECL

if (External_mode==1){
  df["LT_ECL"]<-NA
  for (i in 1:nrow(df)){
    df[i,"LT_ECL"]<-ext_LT_ECL(df[i,])
  }
} else {
  df["LT_ECL"] <- mapply(get_LT_ECL,df$Month_Remaining,df$Rating,df$Exposure,df$LGD,df$EIR,df$Amortization,1,row.names(df))
}

#Set ECL either as 12-month ECL or lifetime ECL depending on bucket
df["ECL"] <- mapply(get_ECL,df$Bucket,df$YM_ECL,df$LT_ECL)

#Write deal sheet 
write.table(df[,],outputfile1,sep=";",col.names=T,row.names=F, na="",dec=",")

