# Delete Me Discord

**Delete Me Discord** is a command-line tool designed to help Discord users delete their messages across multiple channels based on time or message count criteria.


>⚠️**Use at Your Own Risk:**
>Using automated tools on Discord, may violate Discord's [Terms of Service](https://discord.com/terms) and could result in account suspension or termination. Please use this tool responsibly and understand the potential risks involved.
## Features

- **Time-Based Deletion:** Delete messages older than a specified time delta.
- **Count-Based Preservation:** Preserve a certain number of recent messages regardless of their age.
- **Selective Channel Processing:** Include or exclude specific channels, guilds, or parent categories.
- **Dry Run Mode:** Simulate deletions without actually removing any messages.
- **Robust Logging:** Track the script's actions and troubleshoot issues effectively.
- **Rate Limit Handling:** Automatically handles Discord API rate limits with customizable retry strategies.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Basic Command](#basic-command)
  - [Command-Line Options](#command-line-options)
  - [Examples](#examples)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)

## Installation

You can install `delete-me-discord` using `pip`. Ensure you have Python 3.6 or higher installed.

### Using `pip`

```bash
pip install delete-me-discord
```


## Usage

After installation, you can execute the script directly from the command line using the `delete-me-discord` command.

### Basic Command

```bash
delete-me-discord --preserve-n 10 --preserve-last "weeks=1,days=3"
```

This command will delete messages older than 1 week and 3 days while preserving at least 10 messages in each channel.

### Command-Line Options

- `--include-ids`:
  **Type:** `str`
  **Description:** List of channel/guild/parent IDs to include.
  **Usage:** `--include-ids 123456789012345678 234567890123456789`

- `--exclude-ids`:
  **Type:** `str`
  **Description:** List of channel/guild/parent IDs to exclude.
  **Usage:** `--exclude-ids 345678901234567890 456789012345678901`

- `--dry-run`:
  **Type:** `flag`
  **Description:** Perform a dry run without deleting any messages. Useful for testing.
  **Usage:** `--dry-run`

- `--log-level`:
  **Type:** `str`
  **Choices:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  **Description:** Set the logging level. Default is `INFO`.
  **Usage:** `--log-level DEBUG`

- `--max-retries`:
  **Type:** `int`
  **Description:** Maximum number of retries for API requests in case of rate limiting. Default is `5`.
  **Usage:** `--max-retries 10`

- `--retry-time-buffer`:
  **Type:** `float` or `float float`
  **Description:** Additional time (in seconds) to wait after rate limit responses. Provide one value or two values for randomness. Default is `[25, 35]`.
  **Usage:**
  - Single value: `--retry-time-buffer 30`
  - Range: `--retry-time-buffer 25 35`

- `--fetch-sleep-time`:
  **Type:** `float` or `float float`
  **Description:** Sleep time (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is `[0.2, 0.4]`.
  **Usage:**
  - Single value: `--fetch-sleep-time 0.3`
  - Range: `--fetch-sleep-time 0.2 0.4`

- `--delete-sleep-time`:
  **Type:** `float` or `float float`
  **Description:** Sleep time (in seconds) between message deletion attempts. Provide one value or two values for randomness. Default is `[1.5, 2]`.
  **Usage:**
  - Single value: `--delete-sleep-time 1.75`
  - Range: `--delete-sleep-time 1.5 2`

- `--preserve-n`:
  **Type:** `int`
  **Description:** Number of recent messages to preserve in each channel regardless of `--preserve-last`. Default is `12`.
  **Usage:** `--preserve-n 15`

- `--preserve-last`:
  **Type:** `str`
  **Description:** Preserves recent messages within the last given delta time (e.g., `"weeks=2,days=3"`) regardless of `--preserve-n`. Default is `weeks=2`.
  **Usage:** `--preserve-last "weeks=1,days=3"`

### Examples

#### 1. Delete Messages Older Than 2 Weeks and Preserve at least Last 10 Messages

```bash
delete-me-discord --preserve-n 10 --preserve-last "weeks=2"
```

#### 2. Perform a Dry Run to See Which Messages Would Be Deleted

```bash
delete-me-discord --dry-run
```

#### 3. Delete Messages in Specific Channels Only

```bash
delete-me-discord --include-ids 123456789012345678 234567890123456789 --preserve-last "weeks=1"
```

#### 4. Exclude Specific Guilds from Deletion

```bash
delete-me-discord --exclude-ids 345678901234567890 --preserve-n 5
```

#### 5. Increase Retry Attempts and Adjust Rate Limit Buffer

```bash
delete-me-discord --max-retries 10 --retry-time-buffer 30 40 --preserve-n 20
```

## Configuration

Before using `delete-me-discord`, you need to set up your Discord credentials by defining the following environment variables:

- **`DISCORD_TOKEN`**: Your Discord authorization token. See [this guide](https://github.com/victornpb/undiscord/wiki/authToken) to obtain your token.
- **`DISCORD_USER_ID`**: Your Discord user ID. This ID is used to target messages authored by you. You can obtain it by enabling Developer Mode in Discord and right-clicking your username to copy the ID.

**Security Note:**
Never share your authorization token. Sharing it will allow others to access your account and perform actions on your behalf.

## Contributing

Contributions are welcome! If you'd like to improve `delete-me-discord`, please follow these steps:

1. **Fork the Repository:** Click the "Fork" button on the repository page to create your own fork.

2. **Clone Your Fork:**

   ```bash
   git clone https://github.com/janthmueller/delete-me-discord.git
   cd delete-me-discord
   ```

3. **Create a New Branch:**

   ```bash
   git checkout -b feature/YourFeatureName
   ```

4. **Make Your Changes:** Implement your feature or fix bugs.

5. **Commit Your Changes:**

   ```bash
   git commit -m "Add feature: YourFeatureName"
   ```

6. **Push to Your Fork:**

   ```bash
   git push origin feature/YourFeatureName
   ```

7. **Open a Pull Request:** Navigate to the original repository and click "New Pull Request."

### Reporting Issues

If you encounter any bugs or have suggestions for improvements, please open an issue in the [Issues](https://github.com/janthmueller/delete-me-discord/issues) section of the repository.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.

