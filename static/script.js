document.addEventListener('DOMContentLoaded', () => {
    const apiKeyInput = document.getElementById('api-key');
    const promptInput = document.getElementById('prompt-input');
    const sendBtn = document.getElementById('send-btn');
    const resultDisplay = document.getElementById('result-display');
    const spinner = document.getElementById('spinner');
    const errorMessage = document.getElementById('error-message');
    const placeholder = document.querySelector('.placeholder');

    const md = window.markdownit();

    async function handleSend() {
        const apiKey = apiKeyInput.value.trim();
        const prompt = promptInput.value.trim();

        if (!apiKey || !prompt) {
            showError("请确保 API Key 和梦境内容都已填写。");
            return;
        }

        setLoading(true);
        resultDisplay.innerHTML = '';

        try {
            const response = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                body: JSON.stringify({
                    model: 'dream-interpreter-pro',
                    messages: [{ role: 'user', content: prompt }],
                    stream: true
                })
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || '请求失败');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n\n').filter(line => line.startsWith('data: '));

                for (const line of lines) {
                    const jsonStr = line.substring(6);
                    if (jsonStr.trim() === '[DONE]') {
                        break;
                    }
                    try {
                        const data = JSON.parse(jsonStr);
                        const content = data.choices[0]?.delta?.content;
                        if (content) {
                            fullContent += content;
                            resultDisplay.innerHTML = md.render(fullContent);
                        }
                    } catch (e) {
                        console.error('Error parsing SSE chunk:', e);
                    }
                }
            }
        } catch (error) {
            showError(error.message);
        } finally {
            setLoading(false);
        }
    }

    function setLoading(isLoading) {
        sendBtn.disabled = isLoading;
        promptInput.disabled = isLoading;
        spinner.classList.toggle('hidden', !isLoading);
        if (isLoading) {
            placeholder?.classList.add('hidden');
            hideError();
        }
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
    }

    function hideError() {
        errorMessage.classList.add('hidden');
    }

    sendBtn.addEventListener('click', handleSend);
    promptInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });
});
