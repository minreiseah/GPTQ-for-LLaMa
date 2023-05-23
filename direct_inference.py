from llama_inference import LLM

model_path = "models/alpaca-native-4bit"
load_path = "models/alpaca-native-4bit/alpaca7b-4bit.pt"

llm = LLM(model_path=model_path, load_path=load_path)

query = "where is singapore?"

llm.generate(query)