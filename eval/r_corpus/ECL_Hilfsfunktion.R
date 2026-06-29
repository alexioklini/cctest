rm(list=ls())

readcsv2<-function(filename,convmat=T,kopf=F,readtime=0,removecolcsv=0){
  time<-"Months"
  splittet<-strsplit(filename,"\\.")[[1]]
  filetype<-splittet[length(splittet)]
  if(filetype=="csv"){
   result<-read.table(filename,sep=";",header=kopf)
    if((removecolcsv>0)){result<-result[,-c(removecolcsv)]}
    if(readtime==1){
     if(pdtime=="PD_input_yearly"){
        time<-"Years"
        }
      }
    }
  if(convmat==T){result<-as.matrix(result)}
  if(time=="Years"){
    M_cpd_y<-result
    M_cpd_y<-cbind(rep(0,nrow(M_cpd_y)),M_cpd_y)
    M_cpd<-NULL
    #
      for(i in(1:(ncol(M_cpd_y)-1))){
        for(j in 0:11){
          if(interpol=="first_order"){
            M_cpdy<-cbind(M_cpd,M_cpd_y[,i]*(1-j/12)+M_cpd_y[,i+1]*j/12)
            }else if (interpol=="second_order"){
              jj=j
              t1=i
              if(i==(ncol(M_cpd_y)-1)){jj=jj+12
              t1=t1-1}
              L0=(jj/12-1)*(jj/12-2)/2
              L1=(jj/12)*(2-jj/12)
              L2=(jj/12)*(jj/12-1)/2
              t2=t1+1
              t3=t1+2
              M_cpd<-cbind(M_cpd,L0*M_cpd_y[,t1]+L1*M_cpd_y[,t2]+L2*M_cpd_y[,t3])
              }
          }
        }
    result<-M_cpd[,-1]
    }
  return(result)
  }


get_12M_ECL<-function(Month_Remaining,rating,exposure,LGD,EIR,amort,port){
 if(Month_Remaining==0){d_ecl=0}else{
    jahre_echt<-min(Month_Remaining/12,1)
    rem_time<-min(Month_Remaining,12)
    PD_1<-PD_cube[rating,rem_time,port]
    d_ecl<-exposure*LGD*(PD_1)/(1+EIR)^jahre_echt
    }
  return(d_ecl)
}


ext_LT_ECL<-function(row_i){
  if (row_i$Month_Remaining==0) {d_ecl=0}else{
    jahre<-floor(row_i$Month_Remaining/12)
    jahre_echt<-row_i$Month_Remaining/12
    rem_month<-row_i$Month_Remaining - jahre*12
    ead_col<-grep("EAD_",colnames(row_i))
    lgd_col<-grep("LGD_",colnames(row_i))
    d_ecl<-0.0
    if(jahre>=1){
     for(j in 1:jahre){
        PD_1<-PD_cube[row_i$Rating,j*12,1]
        PD_2<-0
        if(j-1>0){PD_2<-PD_cube[row_i$Rating,(j-1)*12,1]}
        marginalePD<-(PD_1-PD_2)
        ead<-row_i[1,ead_col[((j-1)*12+1)]]
        LGD<-row_i[1,lgd_col[((j-1)*12+1)]]
        d_ecl<-ead*LGD*marginalePD/(1+row_i$EIR)^j+d_ecl
        }
      }
    PD_1<-PD_cube[row_i$Rating,row_i$Month_Remaining,1]
    PD_2<-0
    if(jahre>=1) {PD_2<-PD_cube[row_i$Rating,jahre*12,1]}
    if(jahre_echt>jahre){
     ead<-row_i[1,ead_col[jahre*12+1]]
      LGD<-row_i[1,lgd_col[jahre*12+1]]}
    else{
      ead<-0
      LGD<-0
      }
    marginalePD<-(PD_1-PD_2)
    d_ecl<-ead*LGD*marginalePD/(1+row_i$EIR)^(jahre_echt)+d_ecl
    }
  return(d_ecl)
}
 
get_LT_ECL<-function(Month_Remaining,rating,exposure,LGD,EIR,amort,port,zeilenname){
   if(Month_Remaining==0){d_ecl=0}else{
      jahre<-floor(Month_Remaining/12)
      jahre_echt<-Month_Remaining/12
      rem_month<-Month_Remaining - jahre*12
      if(as.integer(zeilenname)%%1000==0){print(zeilenname)}
      d_ecl<-0.0
      if(jahre>=1){
       for(j in 1:jahre){
          PD_1<-PD_cube[rating,j*12,port]
          PD_2<-0
          if(j-1>0){PD_2<-PD_cube[rating,(j-1)*12,port]}
           
            marginalePD<-(PD_1-PD_2)
            exp_f=get_ead_f(EIR,jahre_echt,j-1,amort)
            ead<-exposure*exp_f
            d_ecl<-ead*LGD*marginalePD/(1+EIR)^j+d_ecl
            }
        }
      PD_1<-PD_cube[rating,Month_Remaining,port]
      PD_2<-0
      if(jahre>=1) {PD_2 <- PD_cube[rating,jahre*12,port]}
      exp_f=get_ead_f(EIR,jahre_echt,jahre,amort)
      marginalePD<-(PD_1-PD_2)
      ead=exposure*exp_f
      d_ecl<-ead*LGD*marginalePD/(1+EIR)^(jahre_echt)+d_ecl
      }
    return(d_ecl)
}
 
   
get_ead_f<-function(x,n,i,amortisierung){
   if(amortisierung=="Annuity"){
      i=i+1
      ead_f=((1+x)^n-(1+x)^(i-1))/((1+x)^n-1)
      }
    else if (amortisierung=="Linear"){
      ead_f=1-1/n*i
      }
    else{ead_f=1}
    return(max(0,ead_f))
}
 
get_ECL<-function(buck,YM_ecl,LT_ecl){
    ECL<-YM_ecl
    if(buck==2){ECL<-LT_ecl}
    return(ECL)
}
 
   