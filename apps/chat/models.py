from django.db import models
from django.contrib.auth import get_user_model
from users.models.partners import Partner

User = get_user_model()


class Message(models.Model):
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_messages'
    )
    receiver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_messages'
    )
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = 'chat'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sender', 'receiver']),
            models.Index(fields=['receiver', 'is_read']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"Message from {self.sender} to {self.receiver} at {self.created_at}"


class Conversation(models.Model):
    admin_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='chat_conversations',
    )
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name='chat_conversations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'chat'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['admin_user', 'partner'], name='unique_admin_partner_conversation')
        ]
        indexes = [
            models.Index(fields=['admin_user', '-updated_at']),
            models.Index(fields=['partner', '-updated_at']),
        ]

    def __str__(self):
        return f"Conversation admin={self.admin_user_id} partner={self.partner_id}"


class ChatMessage(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender_admin = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_messages_sent',
    )
    sender_partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_messages_sent',
    )
    receiver_admin = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_messages_received',
    )
    receiver_partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_messages_received',
    )
    content = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'chat'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['receiver_admin', 'is_read']),
            models.Index(fields=['receiver_partner', 'is_read']),
        ]

    @property
    def sender_type(self):
        return 'admin' if self.sender_admin_id else 'partner'

    @property
    def receiver_type(self):
        return 'admin' if self.receiver_admin_id else 'partner'

    @property
    def sender_id(self):
        return self.sender_admin_id or self.sender_partner_id

    @property
    def receiver_id(self):
        return self.receiver_admin_id or self.receiver_partner_id

    def __str__(self):
        return f"ChatMessage #{self.id} in conversation {self.conversation_id}"
