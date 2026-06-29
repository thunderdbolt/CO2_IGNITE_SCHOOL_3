import pandas as pd
import io
import re
from collections import OrderedDict

class trend:
    def __init__(self, Trend_file_path):
        self.Trend_file_path = Trend_file_path
        self.location = self.Location()
        self.get_variables = self._get_variables()
        self.to_dataframe = self._trend_to_dataframe()
        
        
    def get_value(self, Branch = "NA", Variable="NA", Pipe="NA", Section="NA", Position ="NA", Time = 0.0):
        find_loc = self._find_loc(Branch , Variable, Pipe, Section, Position)
        trend_results_1 = self.to_dataframe.iloc[7:,:]
        for StartTime in range (0, len(trend_results_1[0])):
            if(trend_results_1[0][StartTime] > Time):
              break
        DiffValue =((float(trend_results_1[find_loc][StartTime:StartTime+1]))-
                    (float(trend_results_1[find_loc][StartTime-1:StartTime])))
        DiffTime = (float(trend_results_1[0][StartTime:StartTime+1])-
                    float(trend_results_1[0][StartTime-1:StartTime]))
        Value = ((float(trend_results_1[find_loc][StartTime-1:StartTime])) + 
                    (DiffValue*(Time-float(trend_results_1[0][StartTime-1:StartTime]))/DiffTime))
        return(Value)
        
        

    def _get_variables (self):
        if type(self.Trend_file_path) == str:
            location =[]
            location.append(self.Trend_file_path)
        else :
            location = self.Trend_file_path
        for LOC in range (0, len(location)):
    
            xx = location[LOC]
            EOL =0
            with open(xx, "r") as ins:
                array = []
                for line in ins:
                    line = line.rstrip("\n")
                    array.append(line)
                    EOL =EOL+1
        
            EOL= EOL-1
            var = 1
            yy = 'CATALOG '
            BRANCH =[]
        
            while array[var] != yy:
                if(array[var] == "BRANCH"):
                    BRANCH.append(array[var+1].replace("'","")) 
                var = var + 1
                if var > EOL:
                        break
                        
            cat = var +1
        
            CatStart = cat+1
            CatNum = int(array[cat])
            CatEnd = cat + CatNum 
        
            global varname
            varname = []
            global varbranch
            varbranch = []
            Pipe = []
            PipeSection = []
            Unit = []
            UnitName = []
            vartype =[]
            #SECTION ={vartype=}
        
        
            for x in range(CatStart, CatEnd+1):
                data =[]
                data =array[x].split("'")
        
                while ' ' in data:
                    data.remove(' ')
        
                while ' ' in data:
                    data.remove('')
        
                varname.append(data[0].rstrip())
                if data[1] == 'SECTION:':
                    vartype.append('SECTION')
                    varbranch.append( data[3]) #Branch Name
                    Pipe.append( data[5]) #Pipe Name
                    PipeSection.append( data[7]) # Pipe No.
                    Unit.append( data[9].strip('(').strip(')')) #Units
                    UnitName.append( data[10]) #Variable
        
                elif data[1] == 'BRANCH:':
                    vartype.append('BRANCH') 
                    varbranch.append( data[2]) #Branch Name
                    Pipe.append( "NaN") #Pipe Name
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[3].strip('(').strip(')')) #Units
                    UnitName.append( data[4]) #Variable
        
                elif data[1] == 'BOUNDARY:':
                    vartype.append('BOUNDARY')
                    varbranch.append( data[3]) #Branch Name
                    Pipe.append( data[5]) #Pipe Name
                    PipeSection.append( data[7]) # Pipe No.
                    Unit.append( data[9].strip('(').strip(')')) #Units
                    UnitName.append( data[10]) #Variable
        
                elif data[1] == 'SOURCE:':
                    vartype.append('SOURCE')
                    varbranch.append( data[2]) #Branch Name
                    Pipe.append( "NaN") #Pipe Name
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[3].strip('(').strip(')')) #Units
                    UnitName.append( data[4]) #Varivarbranchable
        
                elif data[1] == 'POSITION:':
                    vartype.append('POSITION')
                    varbranch.append( data[2]) #Branch Name
                    Pipe.append( "NaN") #Pipe Name
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[3].strip('(').strip(')')) #Units
                    UnitName.append( data[4]) #Varivarbranchable
        
                elif data[1] == 'GLOBAL':
                    vartype.append('GLOBAL')
                    varbranch.append( "NaN") #Branch Name
                    Pipe.append( "NaN") #Pipe Name
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[2].strip('(').strip(')')) #Units
                    UnitName.append( data[3]) #Variable
        
                elif data[1] == 'NODE:':
                    vartype.append('NODE')
                    varbranch.append( data[2]) #Branch Name
                    Pipe.append( "NaN") #Pipe Name
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[3].strip('(').strip(')')) #Units
                    UnitName.append( data[4]) #Variable
        
                else:
                    vartype.append(data[1].rstrip(':'))
                    varbranch.append( data[2]) #Branch Name
                    Pipe.append( "NaN") #Pipe Namev
                    PipeSection.append( "NaN") # Pipe No.
                    Unit.append( data[3].strip('(').strip(')')) #Units
                    UnitName.append( data[4]) #Varivarbranchable
        
            varname.insert(0,"Time")
            vartype.insert(0,"Time")
            varbranch.insert(0,"NaN") #Branch Name
            Pipe.insert(0,"NaN") #Pipe Namev
            PipeSection.insert(0,"NaN") # Pipe No.
            Unit.insert(0,"S") #Units
            UnitName.insert(0,"Time") #Varivarbranchable
            #return {'varname': varname, 'vartype': vartype ,'y2': y2}
        
        sales = OrderedDict([("Variable_Name" ,varname),
                                ("Variable_Type" ,vartype),
                                ("Variable_Branch" ,varbranch),
                                ("Variable_Pipe_Name" ,Pipe),
                                ("Variable_Pipe_Section" ,PipeSection),
                                ("Variable_Unit" ,Unit),
                                ("Variable_UnitName" ,UnitName)])
    
        df = pd.DataFrame.from_dict(sales)
        return(df)
    
    def Location(self):
        if type(self.Trend_file_path) == str:
            location =[]
            location.append(self.Trend_file_path)
        else :
            location = self.Trend_file_path
        return (location)
    
    def _trend_to_dataframe_1(self):
        import pandas as pd
        if type(self.Trend_file_path) == str:
            location =[]
            location.append(self.Trend_file_path)
        else :
            location = self.Trend_file_path
            
        for LOC in range (0, len(location)):
            xx = location[LOC]
            EOL =0
            with open(xx, "r") as ins:
                array = []
                for line in ins:
                    line = line.rstrip("\n")
                    array.append(line)
                    EOL =EOL+1
        
            EOL= EOL-1
            var = 1
            yy = 'CATALOG '
            BRANCH =[]
        
            while array[var] != yy:
                if(array[var] == "BRANCH"):
                    BRANCH.append(array[var+1].replace("'","")) 
                var = var + 1
                if var > EOL:
                        break
                        
            cat = var +1
        
            CatStart = cat+1
            CatNum = int(array[cat])
            CatEnd = cat + CatNum 
            
            # Extract only the numerical data block
            data_lines = array[CatEnd+2:]
            
            # Force a space before any + or - sign that immediately follows a digit.
            # This safely fixes "0.160-50.000" into "0.160 -50.000" without breaking scientific notation like "E-02".
            fixed_lines = [re.sub(r'(?<=\d)([+-])', r' \1', line) for line in data_lines]
            
            # Feed the corrected text directly into pandas
            xc = pd.read_csv(io.StringIO("\n".join(fixed_lines)), sep=r"\s+", header=None)
            #xc.columns =self.get_variables()["Variable_Name"].tolist()
            
            for StartTime in range (7, len(xc[0])):
                if(xc[0][StartTime] > xc[0][len(xc)-1]-7200):
                  break
            return(xc)
        
    def _trend_to_dataframe(self):
        import pandas as pd
        if type(self.Trend_file_path) == str:
            location =[]
            location.append(self.Trend_file_path)
        else :
            location = self.Trend_file_path
            
        for LOC in range (0, len(location)):
            xx = location[LOC]
            EOL =0
            with open(xx, "r") as ins:
                array = []
                for line in ins:
                    line = line.rstrip("\n")
                    array.append(line)
                    EOL =EOL+1
        
            EOL= EOL-1
            var = 1
            yy = 'CATALOG '
            BRANCH =[]
        
            while array[var] != yy:
                if(array[var] == "BRANCH"):
                    BRANCH.append(array[var+1].replace("'","")) 
                var = var + 1
                if var > EOL:
                        break
                        
            cat = var +1
        
            CatStart = cat+1
            CatNum = int(array[cat])
            CatEnd = cat + CatNum 

            
            # Extract only the numerical data block
            data_lines = array[CatEnd+2:]
            
            # Force a space before any + or - sign that immediately follows a digit.
            # This safely fixes "0.160-50.000" into "0.160 -50.000" without breaking scientific notation like "E-02".
            fixed_lines = [re.sub(r'(?<=\d)([+-])', r' \1', line) for line in data_lines]
            
            # Feed the corrected text directly into pandas
            xc = pd.read_csv(io.StringIO("\n".join(fixed_lines)), sep=r"\s+", header=None)
            
            #xc.columns =self.get_variables()["Variable_Name"].tolist()
            mm =self.get_variables.transpose()
            #mm = mm.transpose()
            
            nn = pd.concat([mm, xc], ignore_index=True)
            
            for StartTime in range (7, len(xc[0])):
                if(xc[0][StartTime] > xc[0][len(xc)-1]-7200):
                  break
        return(nn)
        
                       
    def _find_loc(self, Branch = "NA", Variable="NA", Pipe="NA", Section="NA", Position ="NA"):
        df = self.get_variables
        #print (df)
        try :
            if ((Branch != "NA") & (Variable != "NA") & (Pipe != "NA") & (Section != "NA")):
                i= df[(df["Variable_Branch"]==Branch) & (df["Variable_Name"]==Variable) & (df["Variable_Pipe_Name"]==Pipe) & 
                (df["Variable_Pipe_Section"]==Section)].index.values.astype(int)[0]
                return (i)
            elif (Position != "NA"):
                i= df[(df["Variable_Name"]==Variable) & (df["Variable_Branch"]==Position)].index.values.astype(int)[0]
                return (i) 
            elif ((Branch != "NA") & (Variable != "NA")):
                i= df[(df["Variable_Branch"]==Branch) & (df["Variable_Name"]==Variable)].index.values.astype(int)[0]
                return (i)
                
        except:
            print ("Variable not present")
            return("")
        
   
    def _StartTime(self):
        trend_results_1 = self.to_dataframe().iloc[7:,:]
        for StartTime in range (0, len(trend_results_1[0])):
            if(trend_results_1[0][StartTime] > trend_results_1[0][len(trend_results_1)-1]-7200):
              break
        return(StartTime)