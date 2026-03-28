from agents.dictionary_agent import save_definition, lookup_definition

seed_data = {
    "latency": "The delay between a request and response in a network.",
    "bandwidth": "The maximum rate of data transfer across a network.",
    "api": "A set of rules that allows software applications to communicate.",
    "token": "A unit of text processed by an AI model.",
    "embedding": "A numerical representation of text used for semantic search.",
    "vector database": "A database optimized for storing and searching embeddings."
}

for word, definition in seed_data.items():
    save_definition(word, definition)

print(lookup_definition("latency"))

