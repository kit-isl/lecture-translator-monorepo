# Bot



## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.


```
cd existing_repo
git remote add origin https://git.scc.kit.edu/isl/lt-middleware/bot.git
git branch -M main
git push -uf origin main
```

## BOT

The bot component allows users to chat with off-the-shelf LLM's. The current version enables creating a content database and use it for Retrieval Augmented Generation. The Vector Stores are saved after being created initially and are not rebuilt from the raw text even if the container gets recreated. If the implementation is changed, they need to be deleted manually!

For ease of access, the bot can be configured with different prompts and contents. To adapt for your bot, modify the apps.json file. This contains the metadata and will *ONLY WORK* if the data sent has key "bot" which matches a entry in the json.

If your app name is kitcat, make sure the data you send to the bot component contains the following:

```
data["bot"] = "kitcat" # Change to your app name
```

Then, add the following entry in apps.json. For example, this is the following entry of kitcat:
```
{
  "kitcat": {
    "system_prompt": "You are an AI assistant and a digital member of AI4LT lab called KitCat. You behave in a professional cat manner. You use the scraped text from the website to answer the question if it is relevant. You provide short answers as much as possible and refer to URL if more information is necessary.",
    "context_template": "\nRespond to the user query as KitCat and use the scraped text below for knowledge if necessary.\n--------------------\n{context_str}\n--------------------\nDo not say according to the text but respond like a cat and very briefly.",
    "content_directory": "kitcat/ai4lt.content.txt",
    "start_message": "Hello, I am KitCat. I'm purrfectly thrilled to meet you! What do you want to know about the AI4LT Lab?",
    "keyword": "ask kitkat"
  }
}
```


1. Key of the main dict: Application name
2. system_prompt: What the app assistant is?
3. context_template: Formatted text with the context string. Describe what should the model do based on the {context_str}?
4. content_directory: The *full file path* (This should be changed to read all files in directory, currently its a single file) to the context that the LLM should use. This does not include history and will be added automatically
5. start_message: The introduction message the user should see when they open the chat box
6. keyword: The keyword for the app. Sentence after the keyword is treated as a question

Finally copy your content to the Dockerfile
```
COPY kitcat ./kitcat/ 
```

TODO: 

Refer to [issues](https://gitlab.kit.edu/kit/isl-ai4lt/lt-middleware/bot/-/issues) for next steps
