from openai import OpenAI
import os

client = OpenAI()

response = client.responses.create(
    model="gpt-4o-mini",
    input="Say hello"
)

print(response.output_text)
