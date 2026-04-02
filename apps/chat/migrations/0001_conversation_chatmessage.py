from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('users', '__first__'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='Conversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('admin_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_conversations', to='auth.user')),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_conversations', to='users.partner')),
            ],
            options={
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField()),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='chat.conversation')),
                ('receiver_admin', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages_received', to='auth.user')),
                ('receiver_partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages_received', to='users.partner')),
                ('sender_admin', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages_sent', to='auth.user')),
                ('sender_partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages_sent', to='users.partner')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='conversation',
            constraint=models.UniqueConstraint(fields=('admin_user', 'partner'), name='unique_admin_partner_conversation'),
        ),
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(fields=['admin_user', '-updated_at'], name='chat_conver_admin_u_5fe4e0_idx'),
        ),
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(fields=['partner', '-updated_at'], name='chat_conver_partner_3d80c8_idx'),
        ),
        migrations.AddIndex(
            model_name='chatmessage',
            index=models.Index(fields=['conversation', 'created_at'], name='chat_chatme_convers_1f53de_idx'),
        ),
        migrations.AddIndex(
            model_name='chatmessage',
            index=models.Index(fields=['receiver_admin', 'is_read'], name='chat_chatme_receive_576deb_idx'),
        ),
        migrations.AddIndex(
            model_name='chatmessage',
            index=models.Index(fields=['receiver_partner', 'is_read'], name='chat_chatme_receive_4b35f8_idx'),
        ),
    ]
