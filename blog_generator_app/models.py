from django.db import models
from django.contrib.auth.models import User


# Create your models here.
class BlogPost(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE
    )  # Link to the user who created the post
    youtube_title = models.CharField(max_length=300)
    youtube_link = models.URLField()
    generated_content = models.TextField()  # -> textfield for large text
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username + " - " + self.youtube_title
