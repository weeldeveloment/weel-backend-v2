from django.contrib import admin
from .models import Message, Conversation, ChatMessage


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'sender', 'receiver', 'content_preview', 'is_read', 'created_at']
    list_filter = ['is_read', 'created_at']
    search_fields = ['sender__email', 'receiver__email', 'content']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'updated_at']
    
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Content'


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'admin_user', 'partner', 'updated_at', 'created_at']
    search_fields = ['admin_user__email', 'partner__username', 'partner__phone_number']
    list_filter = ['created_at', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'conversation',
        'sender_type',
        'sender_id',
        'receiver_type',
        'receiver_id',
        'is_read',
        'created_at',
    ]
    list_filter = ['is_read', 'created_at']
    search_fields = ['content', 'conversation__partner__username', 'conversation__admin_user__email']
    readonly_fields = ['created_at', 'updated_at']
