from langchain_ollama import OllamaEmbeddings
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


class TopicClassifier:
    def __init__(self):
        self.embedder = OllamaEmbeddings(model="llama3")
        self.topic_examples = {
        "cpu_usage": ["CPU utilization high", "Top queries consuming CPU", "CPU bottleneck detected"],
        "memory_usage": ["Memory allocated", "Buffer cache miss", "Swap usage high"],
        "index_optimization": ["Missing index", "Full table scan", "Index fragmentation"],
        "io_waits": ["I/O wait events", "Disk read latency", "High I/O operations"],
        "locks_contention": ["Deadlock detected", "Lock wait time high", "Blocked sessions"],
        "query_plan_issues": ["Suboptimal execution plan", "Nested loop join", "Sort operation heavy"],
        "generic_info": ["Instance name", "Database version", "Query summary"]
        }

    def get_embedding(self, text):
        return self.embedder.embed_query(text)
    
    def get_topic_embeddings(self):
        topic_embeddings = {topic: [self.get_embedding(e) for e in examples] 
                    for topic, examples in self.topic_examples.items()}
        return topic_embeddings
    
    def classify_chunk(self,chunk_text, topic_embeddings, model="llama3"):
        chunk_vec = np.array(self.get_embedding(chunk_text)).reshape(1, -1)
        scores = {}

        for topic, embeddings in topic_embeddings.items():
            topic_vecs = np.array(embeddings)
            sims = cosine_similarity(chunk_vec, topic_vecs)  # (1, n_examples)
            scores[topic] = np.mean(sims)  # average similarity per topic

        best_topic = max(scores, key=scores.get)
        return best_topic, scores


# Generate embeddings for topic examples


