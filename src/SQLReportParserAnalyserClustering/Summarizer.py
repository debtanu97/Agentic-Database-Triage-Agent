from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

class Summarizer:
    def __init__(self):
        self.llm = ChatOllama(model="gpt-oss:20b")
    
    def summarize(self, text):
        # Generate embeddings for the text
        prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """
                        You are an expert database analyst. 
                        Analyze the given DB performance report chunk.
                        
                        Your task:
                        - List important details from the chunk crucial for analysis and insights.
                        - Identify potential issues such as high CPU usage, full table scans, missing indexes, joins on large tables, suboptimal query structures, and many more such DB performance issues from the chunk.
                        - List important metrics present in the chunk without information loss.
                        - Do not write an introduction or summary.
                        Output format:
                            [details: Important details and parameters retrieved from the chunk,issues: ["issue1", "issue2", ...],metrics: ["metric1 value1", "metric2 value2", ...]]
                        """
                    ),
                    ("human", "{report}\n Analyze the given DB performance report chunk.")
                ]
            ).partial()
        formatted_prompt=prompt.format(report=text)
        raw_response = self.llm.invoke(formatted_prompt)

        try:
            return raw_response.content
        except Exception as e:
            print("Error parsing response", e, "Raw Response - ", raw_response)
    