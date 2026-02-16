document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const chatContainer = document.getElementById('chat-container');
    const modelIndicator = document.getElementById('model-indicator');
    const modelName = document.getElementById('model-name');

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const message = userInput.value.trim();
        if (!message) return;

        // Add User Message
        addMessage(message, 'user');
        userInput.value = '';

        // Show Thinking State
        showThinkingMetadata('Thinking Model');

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message }),
            });

            if (!response.ok) {
                throw new Error('Network response was not ok');
            }

            const data = await response.json();

            // Add System Message
            addMessage(data.response, 'system');

            // Update Model Indicator based on actual model used
            updateModelIndicator(data.model_used);

        } catch (error) {
            console.error('Error:', error);
            addMessage('Sorry, something went wrong. Please try again.', 'system');
            resetModelIndicator();
        }
    });

    function addMessage(text, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', sender);

        const bubble = document.createElement('div');
        bubble.classList.add('bubble');

        // Simple line break handling
        bubble.innerHTML = text.replace(/\n/g, '<br>');

        messageDiv.appendChild(bubble);
        chatContainer.appendChild(messageDiv);

        // Scroll to bottom
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function showThinkingMetadata(name) {
        modelIndicator.classList.remove('hidden', 'fast');
        modelIndicator.classList.add('thinking');
        modelName.textContent = name + " (Processing...)";
    }

    function updateModelIndicator(modelType) {
        modelIndicator.classList.remove('hidden');
        if (modelType === 'thinking') {
            modelIndicator.classList.remove('fast');
            modelIndicator.classList.add('thinking');
            modelName.textContent = 'Thinking Model Used';
        } else {
            modelIndicator.classList.remove('thinking');
            modelIndicator.classList.add('fast');
            modelName.textContent = 'Fast Model Used';
        }
    }

    function resetModelIndicator() {
        modelIndicator.classList.add('hidden');
    }
});
