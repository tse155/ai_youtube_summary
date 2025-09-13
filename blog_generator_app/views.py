from django.shortcuts import render, redirect

# importing user model from django
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
import json
import yt_dlp
import urllib.request
import re
import os
from dotenv import load_dotenv
import assemblyai as aai
import anthropic
from openai import OpenAI
from .models import BlogPost

load_dotenv()  # Load environment variables from .env file


# Create your views here.


# only logged in users can access the index view
@login_required
def index(request):
    return render(request, "blog_generator_app/index.html")


#! functionality views
@csrf_exempt
def generate_blog(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            yt_link = data["link"]
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid data sent"}, status=400)

        # get yt title
        title = yt_title(yt_link)

        # get yt transcript
        transcript = yt_transcript(yt_link)
        if transcript in ("No transcription available", None, ""):
            transcript = alternative_transcript(yt_link)
            if transcript in (None, ""):
                return JsonResponse(
                    {
                        "title": "Error",
                        "content": "No transcription available for this video",
                    },
                    status=500,
                )

        # generate summary content using openai
        # blog_content = generate_summary_content_openai(transcript)

        # generate summary content using claude
        blog_content = generate_summary_content_claude(transcript)

        # troubleshooting blog content
        if not blog_content:
            return JsonResponse(
                {
                    "title": "Error",
                    "content": "Failed to generate blog content from LLM api",
                },
                status=500,
            )

        # save blog post to db
        new_post = BlogPost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=blog_content,
        )

        new_post.save()

        # return blog article as a response
        return JsonResponse({"title": title, "content": blog_content}, status=200)

    else:
        # there
        return JsonResponse({"error": "Invalid request method"}, status=405)


#! Retrieve user's blog posts
def blog_list(request):
    blog_articles = BlogPost.objects.filter(user=request.user).order_by("-created_at")
    return render(
        request, "blog_generator_app/all-blogs.html", {"blog_articles": blog_articles}
    )


#! View for blog details
def blog_details(request, pk):
    blog_article_detail = BlogPost.objects.get(id=pk)

    # verifying whether the connected user is the owner of the blog post article
    if blog_article_detail.user == request.user:
        return render(
            request,
            "blog_generator_app/blog-details.html",
            {"blog_article_detail": blog_article_detail},
        )
    else:
        return redirect("/")


#!Aux views
def yt_title(link):
    """Fetch YouTube video title"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(link, download=False)
        title = info.get("title", None)  # type: ignore
        return title if title else "Unknown Title"


def yt_transcript(link):
    """Fetch YouTube video transcript"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(link, download=False)

        # Check for subtitles
        subtitles = info.get("subtitles", {})  # type: ignore
        automatic_captions = info.get("automatic_captions", {})  # type: ignore

        # Try to get English subtitles first
        if "en" in subtitles:
            subtitle_url = subtitles["en"][0]["url"]
        elif "en" in automatic_captions:
            subtitle_url = automatic_captions["en"][0]["url"]
        else:
            return "No transcription available"

        # Fetch the subtitle content
        response = urllib.request.urlopen(subtitle_url)
        subtitle_content = response.read().decode("utf-8")

        # Clean up the subtitle content (remove timestamps and formatting)
        # This is a basic cleanup - you might want to use a proper subtitle parser
        try:
            # Parse the JSON3 data
            subtitle_data = json.loads(subtitle_content)

            # Extract text from events
            transcript_text = ""

            if "events" in subtitle_data:
                for event in subtitle_data["events"]:
                    if "segs" in event:  # segments contain the actual text
                        for seg in event["segs"]:
                            if "utf8" in seg:
                                transcript_text += seg["utf8"]

            return transcript_text.strip()

        except json.JSONDecodeError:
            # If it's not JSON, treat as regular subtitle format

            clean_text = re.sub(r"<[^>]+>", "", subtitle_content)
            clean_text = re.sub(
                r"\d+:\d+:\d+\.\d+ --> \d+:\d+:\d+\.\d+", "", clean_text
            )
            clean_text = re.sub(r"\n+", " ", clean_text)
            return clean_text.strip()


def download_youtube_audio(yt_link):
    """Download audio from YouTube video and return the file path"""
    try:
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)

        final_file = None

        def hook(d):
            nonlocal final_file
            if d["status"] == "finished":
                final_file = d["filename"]

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": f"{settings.MEDIA_ROOT}/%(title)s.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([yt_link])

            return final_file

    except Exception as e:
        print(f"Error downloading audio: {e}")
        return None


def alternative_transcript(link):
    audio_file = download_youtube_audio(link)
    aai_api = os.environ.get("AAI")
    aai.settings.api_key = aai_api

    transcriber = aai.Transcriber()
    transcription = transcriber.transcribe(audio_file)  # type: ignore

    return transcription.text


def generate_summary_content_claude(transcript):
    #! Anthropic setup
    api_key = os.environ.get("CLAUDE_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""
        Based on the following transcript from a YouTube video, generate a summary.
        Make sure the summary is well-structured, engaging, and informative:
        \n\n{transcript}\n\n
    """
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text  # type: ignore


def generate_summary_content_openai(transcript):
    #! OpenAI setup
    api_key = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key)
    prompt = f"""
        Based on the following transcript from a YouTube video, generate a summary.
        Make sure the summary is well-structured, engaging, and informative:
        \n\n{transcript}\n\n
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


#! Authentication views
def user_login(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        # using django's built-in authentication system to verify user credentials
        user = authenticate(request, username=username, password=password)
        if user is not None:
            # if the user is valid, log them in and redirect to home page
            login(request, user)
            return redirect("/")
        else:
            # user is not valid, it is None
            error_message = "Invalid username or password. Please try again."
            return render(
                request,
                "blog_generator_app/login.html",
                {"error_message": error_message},
            )

    return render(request, "blog_generator_app/login.html")


def user_signup(request):
    if request.method == "POST":
        # the user is using POST method ==> the user is clicking the submit button
        # if so, I want to get all details from the form the user just created.
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")
        repeat_password = request.POST.get("repeatPassword")

        if password == repeat_password:
            # create the user
            try:
                user = User.objects.create_user(
                    username=username, email=email, password=password
                )
                user.save()
                # log the user in and redirect to home page
                login(request, user)
                return redirect("/")
            except Exception as e:
                error_message = f"An error occurred during signup: {str(e)}"
                return render(
                    request,
                    "blog_generator_app/signup.html",
                    {"error_message": error_message},
                )
        else:
            # show an error message
            error_message = "Passwords do not match. Please try again."
            return render(
                request,
                "blog_generator_app/signup.html",
                {"error_message": error_message},
            )
    return render(request, "blog_generator_app/signup.html")


def user_logout(request):
    # loging the user out
    logout(request)
    return redirect("/")
