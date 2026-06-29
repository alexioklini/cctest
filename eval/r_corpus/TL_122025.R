#R-Code for transfer logic including stage allocation of transactions

rm(list=ls())

#Presteps: Define input data, output folders, and require relevant R-packages
# require('lubridate') 


#The following input data is specified:

#1) Monthly PIT Lifetime PD (path must be specified) (does not need to be monthly data)
df<-read.table("W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung/output files/122025/PIT/PIT_for_TL.csv",sep=";",header=T)

#2) Output folder: Determines the folder, where the output files will be saved in the last step (path must be specified)
pfad<-"W:/COLLABORATION/Risk_Public/IFRS 9/Berechnungen_Wertminderung"

#3) Identification date (automatically generates the actual date)
id_date<-Sys.Date()

#4) Deal data: deals sheet to which stages will be assigned on transaction level
deals<-read.csv2("W:/COLLABORATION/Risk_Public/IFRS 9/Daten_Impairment Test/Daten 202512/ECL_Inputdaten_2025-12_Konzern.csv",sep=";",header=T, stringsAsFactors = FALSE, fileEncoding="latin1", dec=",") 

#5) Threshold for stage transfer (variables in brackets are the limits of rating class, age and remaining maturity in months, which have to be specified)
threshold<-array(dim=c(18,300,300))
threshold[,,]<-1.5

#6) Set reporting date
rep_date<-as.Date("2025-12-31")

#manuell alle Stichtagsangaben aktualisieren

#Transform dates in R format
deals$Origination_Date<-as.Date(deals$Origination_Date,"%d.%m.%Y")
df$Valid_From<-as.Date(df$Valid_From,"%d.%m.%Y")
deals$Original_Rating<-as.numeric(deals$Original_Rating)


#7) Transfer logic based on comparison of "lifetime" PD or on "12 months" PD
    
transferlogic<-"lifetime" 
#Start of calculation
#Loop over all deals
    
for (i in 1:nrow(deals)){
  #-----------------------------------------------------------
  # Initialise the stage of the deal as 1 and overwrite it if one of the triggers applies
  deals[i,"rstage"]<-"1"
  #-----------------------------------------------------------
  #Current PD
  #Search for all PD-curves of the same rating as current rating of the deal, same segment as the deal and with a Valid_From date before reporting date
  cur_rat<-subset(df, df$Rating == deals[i,"Rating"]& df$Valid_From <= rep_date)
  
  #Sort cur_rat 1. decreasing w.r.t. Valid_From, 2. Increasing w.r.t. PERIOD_COUNT
  cur_rat<-cur_rat[order(-as.numeric(cur_rat$Valid_From)),]
  #First entry has now the correct Valid_From date, drop all entries with a different date
  cur_rat<-cur_rat[1,]
  
  #If no PD curve could be found, break out of loop
  if (nrow(cur_rat)==0){
    print(paste("No actual PD curve found for Deal ",deals[i,"Asset_ID"], sep=""))
    deals[i,"rstage"]<-NA
    break
  }
  
  #Calculate cumulative PD curve
  cum_PD<-as.vector(unlist(cur_rat[,3:ncol(cur_rat)]))
  
  #Set month remaining
  if (deals[i,"Month_Remaining"]!=0){
    month_rem<-deals[i,"Month_Remaining"]
  } else {
    month_rem<-1
  }
  if (transferlogic=="12 months" & deals$Month_Remaining[i]>12){
    month_rem <- 12
  }
  
  #Numerator of transfer criteria = cum_PD at the position month_remaining, if month_remaining = 0 take one month PD
  numerator<-cum_PD[month_rem]
  
  #-------------------------------------------------------------
  #PD at origination
  #Search for all PD curves of the same rating as original rating of the deal, same segment as the deal and with a Valid_From date before the origination date
  orig_rat<-subset(df,df$Rating==deals[i,"Original_Rating"] & df$Valid_From <= deals[i,"Origination_Date"])
  
  #Sort orig_rat 1. decreasing w.r.t. Valid_From 2. Increasing w.r.t. PERIDO_COUNT
  orig_rat<-orig_rat[order(-as.numeric(orig_rat$Valid_From)),]
  #First entry has now the correct Valid_From date, drop all entries with a different date
  orig_rat<-subset(orig_rat, orig_rat$Valid_From==orig_rat$Valid_From[1])
  
  
  #If no PD curve could be found, break out of loop
  if (nrow(orig_rat)==0){
    print(paste("No original PD curve found for Deal ",deals[i,"Asset_ID"], sep=""))
    deals[i,"rstage"]<-NA
    break
  }
  
  #Calculate cumulative PD curve
  cum_origPD<-as.vector(unlist(orig_rat[,3:ncol(orig_rat)]))
  
  #Calculate age of the deal in months
    age_month<-(2025 - deals[i,"Years"]  )*12   +   12 - deals[i,"Months"]
  # Survival probability up to reporting date = 1- cum_origPD at position age_month
  
  if (age_month==0){
    surv_prob<-1
    denominator<-cum_origPD[(age_month+month_rem)]/surv_prob
  } else {
    surv_prob<-1-cum_origPD[age_month]+0.00000001
    denominator<-(cum_origPD[(age_month+month_rem)]-cum_origPD[(age_month)])/surv_prob
  }
  
  
  #----------------------------------------------------------------------------
  #Calculate ratio and compare with threshold
  numerator<-ifelse(numerator==0,0.00000000001,numerator)
  denominator<-ifelse(denominator==0,0.00000000001,denominator)
  
  age_month <- ifelse(age_month==0,1,age_month)
  
  if (numerator/denominator > threshold[deals$Original_Rating[i],age_month,deals[i,"Month_Remaining"]]){
    deals[i,"rstage"]<-"2"
  }
  
  #---------------------------------------------------------------------------
  #Low Credit Risk Exemption
  #if (TRUE %in% (deals[i,"Rating"]<4)){ # 4=Insvetmentgrade
  #  deals[i,"rstage"]<-"1"  
  #}
  
  #----------------------------------------------------------------------------
  #Qualitative trigger
  if (TRUE %in% (deals[i,c("DAYSPD","forbearance","OtherTrigger")]==1)){
    deals[i,"rstage"]<-"2"
  }
  deals[i,"numerator"] <- numerator
  deals[i,"denominator"] <- denominator
  deals[i,"quantitative_ratio"] <- numerator/denominator
}


deals_stage2 <- deals[deals$rstage=="2",c(1:16,(ncol(deals)-4):ncol(deals))]
deals_stage2$qualitative_staging_trigger <- rep(0,nrow(deals_stage2))
for (i in 1:nrow(deals_stage2)){deals_stage2$qualitative_staging_trigger[i] <- ifelse(deals_stage2$DAYSPD[i] == 1 | deals_stage2$forbearance[i] == 1 | deals_stage2$OtherTrigger[i] == 1,1,0)}
deals_stage2$quantitative_staging_trigger <- rep(0,nrow(deals_stage2))
for (i in 1:nrow(deals_stage2)){deals_stage2$quantitative_staging_trigger[i] <- ifelse(deals_stage2$quantitative_ratio[i] > 1.5 ,1,0)}


#Write output data
sys_t<-format(Sys.time(),"%Y_%m_%d_%H_%M_%S")
outdeals <- paste(pfad,"/output files/122025/TL/deals_TL_Konzern",sys_t,".csv",sep="")  #specified output folder for deals sheet including stage allocation
write.table(deals,outdeals,sep=";",row.names=F,col.names=T,na="",dec=",")
    
