// ***********************************************************************
// Project          : Travelers.UI
// Author           : Graim George
// Created          : 03-03-2015
//
// Last Modified By : Graim George
// Last Modified On : 12-12-2016
// ***********************************************************************
// <summary>This module contains all the methods, events used by camera-video feature</summary>
// ***********************************************************************
var cameraVideo = new function () {
 

    var cameras = '';
        
    //popup template header
    var htmlCarouselHeader = '<div class="carousel-header">' +
                                '<div id="popup-carousel-header{INDEX}"></div>' +
                                '<button type="button" class="btn btn-danger btn-xs btn-xxs" id="btn-close-popup-carousel{INDEX}">X</button>' +
                            '</div>';

    //popup template footer
    var htmlCarouselFooter = '<div class="carousel-footer">' +
                                '<button class="btn btn-primary btn-xs" type="button" id="btn-move-prev{INDEX}">Prev</button>&nbsp;&nbsp;' +
                                '<button class="btn btn-primary btn-xs" type="button" id="btn-move-next{INDEX}">Next</button>&nbsp;&nbsp;' +
                                '<button type="button" class="btn btn-danger btn-xs" id="btn-view-video{INDEX}">Video</button>&nbsp;&nbsp;' +
                                '<button class="btn btn-danger btn-xs" type="button" id="btn-view-camera{INDEX}">Camera</button>&nbsp;&nbsp;' +
                                //'<image id="btn-add-rem-favorites{INDEX}"></button>' +
                                '<button class="btn btn-primary btn-xs" type="button" id="btn-add-rem-favorites{INDEX}"></button>' +
                            '</div>';
    //popup template
    var htmlTemplateWithVideo = '<div class="item {CLS_ACTIVE}">{HEADER}' +
                                        '<div id="video-space{INDEX}"  style="display:none; min-height:235px"></div>' +
                                        '<div id="camera-space{INDEX}"><img src="{IMG_URL}" alt="Camera" style="width:100%; height:235px; min-height:235px"></div>' +
                                    '{FOOTER}</div>';

    /*setup options for jw player*/
    var setupJwOptions = function (i, camera, jwSetupOptions) {
        jwSetupOptions.image = utils.getCameraCurrentImageUrl(camera.Type, camera.ImageUrl) + '?' + getTimeStamp();
        jwSetupOptions.playlist[0].sources[0].file = utils.getWowsaUrl().replace("{stream}", camera.ImageUrl.replace('.jpg', '.stream'));
    }

    /*Get popup slide */
    var getCameraVideoSlide = function (i, camera)
    {
        var clsActive = (i == 0) ? "active" : "";

        var slideHtml = htmlTemplateWithVideo.replaceAll('{CLS_ACTIVE}', clsActive).replaceAll('{INDEX}', i).replace('{IMG_URL}', utils.getCameraCurrentImageUrl(camera.Type, camera.ImageUrl) + '?' + getTimeStamp());
        var header = htmlCarouselHeader.replaceAll('{INDEX}', i);
        slideHtml = slideHtml.replace('{HEADER}', header);
        
        var footer = htmlCarouselFooter.replaceAll('{INDEX}', i);
        slideHtml = slideHtml.replace('{FOOTER}', footer);

        return slideHtml;
    }

    /*Attach slide events */
    var attachSliderEvents = function (i, camera, camerasTotal) {

        $('#btn-view-camera' + i).on('click', (function (event) {
            event.Camera = camera;
            jwplayer('video-space' + i).stop();
            switchToCameraOrVideoView("camera", i);
        }));

        if (camera.Type == "sdot") {
            $('#btn-view-video' + i).on('click', (function (event) {
                event.Camera = camera;
                var jwSetupOptions = {
                    "playlist": [
                    {
                        "sources": [
                          {
                              "default": false,
                              //"file": utils.getWowsaUrl(),
                              "type": "hls",
                              "label": "0",
                              "preload": "none"
                          }
                        ]
                    }
                    ],
                    "autostart": true,
                    "height": '100%',
                    "width": '100%',
                    "stretching": 'fill',
                    "aspectratio": '12:7.82',
                    "controls": 'true',
                    "controlbar": 'none',
                    "duration": 300,
                    "primary": "html5",
                    "hlshtml": true,
                    events: {
                        onPause: function (event) { stopJwPlayer() }
                    }
                };

                setupJwOptions(i, camera, jwSetupOptions);
                switchToCameraOrVideoView("video", i);
                jwplayer('video-space' + i).setup(jwSetupOptions);
                //jwplayer().onError(function () {});

            }));
        }
        else if (camera.Type == "wsdot") {
            $('#btn-view-video' + i).css('visibility','hidden');
            $('#btn-view-camera' + i).css('visibility', 'hidden');
        }
        
        var multiplicity = (cameras.length > 1 && i < camerasTotal-1) ? " (cont...)" : "";
        $('#popup-carousel-header' + i).html(camera.Description + multiplicity);
        $('#btn-close-popup-carousel' + i).on('click', (function (e) { utils.showPopupCarousel(false, 'camera'); }));

        //adjust favorites button based on camera selected
        customizeFavoritesButton($('#btn-add-rem-favorites' + i), camera);

        if (i == 0)
        {
            $('#btn-move-prev' + i).prop("disabled", true);
        }

        if (i == camerasTotal-1) {
            $('#btn-move-next' + i).prop("disabled", true);
        }

        $('#btn-move-next' + i).click(function () {
            resetCameraCarouselView();
            $('#popup-carousel').carousel('next');
        });
        $('#btn-move-prev' + i).click(function () {
            resetCameraCarouselView();
            $('#popup-carousel').carousel('prev');
        });
    
    }

    /*Customize ADD/REMOVE button for logged-in user */
    var customizeFavoritesButton = function (favElement, camera)
    {
        if (user.config.isLoggedIn) {

            var isCameraAlreadyAddedToFavs = false;

            isCameraAlreadyAddedToFavs = user.config.profile.userProfile.favorites.cameras.doesExist(camera, function (e) {
                return e.Id === camera.Id;
            });

            if (isCameraAlreadyAddedToFavs)
            {
                favElement.text(REM_FROM_FAVS_TEXT)
            }
            else
            {
                favElement.text(ADD_TO_FAVS_TEXT)
            }


            favElement.on('click', (function (event) {

                if (favElement.text() == ADD_TO_FAVS_TEXT) {
                    user.config.profile.userProfile.favorites.cameras.push(camera);
                    favElement.text(REM_FROM_FAVS_TEXT)
                }
                else
                {
                    utils.removeCameraItemFromArray(user.config.profile.userProfile.favorites.cameras, camera);
                    favElement.text(ADD_TO_FAVS_TEXT)
                }

                user.saveProfile();

            }));
        }
        else
        {
            favElement.hide();
        }
    }

    /*Switch between camera or video mode */
    var switchToCameraOrVideoView = function (viewType, i) {

        if(viewType == "camera")
        {
            $("div[id *= 'video-space']").hide();
            $('#camera-space' + i).show();
        }
        else
        {
            $('#camera-space' + i).hide();
            $('#video-space' + i).show();
        }
    }
    /*Change the carousel to camera view */
    var resetCameraCarouselView = function (event) {

        $("div[id *= 'camera-space']").show();
        $("div[id *= 'video-space']").hide();
    }

    /*Timestamp used for camera image refreshing */
    var getTimeStamp = function()
    {
        var d = new Date();
        return (d.getMilliseconds());
    }

    /*Stops jw player */
    var stopJwPlayer = function () {
        for (var i = 0; i < cameras.length; i++) {
            if ($('video-space' + i) != null) {
                jwplayer('video-space' + i).stop();
            }
        }
    }

    return {
        /*Initialize camera slider */
        intializeCameraSlider : function (cameraJson) {

            $('.carousel-inner').html('');

            cameras = cameraJson;

            utils.showHidePopUpCarouselArrow("show");

            var carouselHtml = "";           
            for (var i = 0; i < cameras.length; i++) {
                var camera = cameras[i];

                carouselHtml += getCameraVideoSlide(i, camera);

                //add extra slide to prevent a bootstrap carouse bug
                if (cameras.length == 1)
                {
                    utils.showHidePopUpCarouselArrow("hide");
                }
            }

            $('.carousel-inner').html(carouselHtml);

            var camerasTotal = cameras.length;
            for (var i = 0; i < camerasTotal; i++) {
                attachSliderEvents(i, cameras[i], camerasTotal);
            }

            utils.showPopupCarousel(true, 'camera');
        
        },
        /*Get all cameras in the neighborhood */
        getNeighborhoodFeed: function(neighborhood)
        {
            return ($.ajax({
                url: index.config.appRoot + "api/Map/GetCamerasByNeighborhood?neighborhood=" + neighborhood,
                dataType: 'json',
                async: false,
                success: function (feed) {
                    retFeed = feed;
                }
            }).responseJSON);
        },
        /*Get all cameras in a given address */
        getAddressFeed: function(lat, lng)
        {
            return ($.ajax({
                url: index.config.appRoot + "api/Map/GetCamerasByAddress?latitude=" + lat + "&longitude=" + lng,
                dataType: 'json',
                async: false,
                success: function (feed) {
                    retFeed = feed;
                }
            }).responseJSON);
        },
        /*ADD/REMOVE favorite button customization wrapper */
        customizeFavoritesButton: function (favElement, camera)
        {
            customizeFavoritesButton(favElement, camera);
        }
        
    }

};

