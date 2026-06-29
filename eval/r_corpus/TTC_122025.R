rm(list = ls())

df<-"W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/diverse Dateien/1Y_mm_finapu_2025.csv"
pfad<-"W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/TTC"
id_num<-"1"
nyears<-25
outputfile1<-paste(pfad,"/ttc_lt_PD",id_num,".csv",sep="")
outputfile1m<-paste(pfad,"/ttc_lt_PD_monthly",id_num,".csv",sep="")
matframe<-read.csv(df,sep=";",header=T,dec = ",")
mig1<-as.matrix(matframe)
lt_cPD<-as.matrix(mig1[,ncol(mig1)])
lt_cPD_monthly<-lt_cPD/12
for(j in 2:12){lt_cPD_monthly<-cbind(lt_cPD_monthly,lt_cPD*j/12)}
for(i in 1:nyears){
  t<-mig1%*%lt_cPD[,i]
  lt_cPD<-cbind(lt_cPD,t)
  for(j in 1:12){
   lt_cPD_monthly<-cbind(lt_cPD_monthly,lt_cPD[,i]*(1-j/12)+lt_cPD[,i+1]*(j/12))
   }
  }
row.names(lt_cPD)<-c(1:ncol(mig1))
outlt_cPD<-lt_cPD[-(nrow(mig1)),]
outlt_cPD_monthly<-lt_cPD_monthly[-(nrow(mig1)),]
#write.table(outlt_cPD,file=outputfile1,sep = ";",col.names = F,quote=FALSE)
#write.table(outlt_cPD_monthly,file=outputfile1m,sep = ";",col.names = F,quote=FALSE)



#Interpolation von Ratingklassen

cumpd<-lt_cPD

cumpd<-ifelse(cumpd==0,0.000000001,cumpd)



pd_array=cumpd[,1]

get_rho <- function(pd,rat) {
  rat<-rat+1
  if (pd >= pd_array[rat]) {
    
    
    rho= log(pd)- log(pd_array[rat])
    rho=rho/(log(pd_array[rat+1])-log(pd_array[rat]))
    
    
  }
  
  if (pd < pd_array[rat]) {
    
    
    rho= log(pd)- log(pd_array[rat-1])
    rho=rho/(log(pd_array[rat])-log(pd_array[rat-1]))
    
    
  } 
  
  return(rho)
  
}


get_cumpd <- function(ratpd,t) {
  
  
  diff<-as.vector(pd_array)-ratpd
  ratingnummer<-length(diff[diff<=0])
  
  
  rho <- get_rho(ratpd,ratingnummer)
  
  return(as.numeric(cumpd[ratingnummer,t]^(1-rho) * cumpd[ratingnummer+1,t]^(rho)))
}

cumresult <- NULL

#Hier k?nnen Startpunkte f?r neue Kurven angegeben werden - MANUELLE EINGABE AUS DEM FINAPU MIGRATION MATRIX (DEFAULT COLUMN) 
for (irat in c(0.00015,0.0003,0.00031,0.00035,0.00042,0.00052,0.00069,0.00099,0.0016,0.00287,0.00531,0.00984,0.0182,0.03367,0.0623,0.11525,0.21322,0.4)) {
  newcum <- NULL
  for (itime in 1:26) {
    
    
    newcum<-c(newcum,get_cumpd(irat,itime)) 
    
    
  }
  cumresult<-rbind(cumresult,newcum) 
  
  
}


##Anschauen !!

#PD monthly = PD yearly/12
#cumresult_monthly = cumresult / 12      
cumresult_monthly<-NULL
nyears<-24
cumresult_monthly<-as.matrix(cumresult[,1]/12)
for(j in 2:12){
  cumresult_monthly<-cbind(cumresult_monthly,cumresult[,1]*j/12)
}
for(i in 1:nyears){
  for(j in 1:12){
    cumresult_monthly<-cbind(cumresult_monthly,cumresult[,i]*(1-j/12)+cumresult[,i+1]*(j/12))
  }
}






row.names(cumresult)<-1:18
row.names(cumresult_monthly)<-1:18


#Ende Berechnung

write.table(cumresult,file=outputfile1,sep = ";",col.names = F,quote=FALSE)
write.table(cumresult_monthly,file=outputfile1m,sep = ";",col.names = F,quote=FALSE)




# Das Ergebnis ist in den Daten "cumresult" sichtbar und beinhaltet nun 20 sich nicht ?berschneidende Ratingklassen:
#PD<-1:100

plot(cumresult[18,],ylim = c(0:1),col="White")
lines(cumresult[1,])
lines(cumresult[2,])
lines(cumresult[3,])
lines(cumresult[4,])
lines(cumresult[5,])
lines(cumresult[6,])
lines(cumresult[7,])
lines(cumresult[8,])
lines(cumresult[9,])
lines(cumresult[10,])
lines(cumresult[11,])
lines(cumresult[12,])
lines(cumresult[13,])
lines(cumresult[14,])
lines(cumresult[15,])
lines(cumresult[16,])
lines(cumresult[17,])
lines(cumresult[18,])

